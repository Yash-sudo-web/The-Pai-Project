import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:timezone/data/latest.dart' as tz_data;
import 'package:timezone/timezone.dart' as tz;

/// One notification the server wants shown at a specific moment.
class PlannedNotification {
  const PlannedNotification({
    required this.at,
    required this.kind,
    required this.title,
    required this.body,
  });

  final DateTime at;
  final String kind;
  final String title;
  final String body;

  static PlannedNotification? fromJson(Map<String, dynamic> json) {
    final at = DateTime.tryParse(json['at'] as String? ?? '');
    final body = json['message'] as String?;
    if (at == null || body == null || body.isEmpty) return null;
    return PlannedNotification(
      at: at,
      kind: json['kind'] as String? ?? 'nudge',
      title: json['title'] as String? ?? 'PAI',
      body: body,
    );
  }
}

/// Owns the local-notification plugin: permission, presentation, scheduling.
///
/// Scheduled notifications are handed to iOS, which fires them itself — so
/// they still arrive if the app has since been killed. What the app cannot do
/// is change the text after handing it over, which is why [applyPlan] is
/// called repeatedly rather than once; see `docs/notifications.md`.
class NotificationService {
  final FlutterLocalNotificationsPlugin _plugin =
      FlutterLocalNotificationsPlugin();

  bool _initialised = false;
  bool _permitted = false;

  /// Ids reserved for the scheduled plan. Re-scheduling cancels only these, so
  /// notifications shown by other paths are left alone.
  static const int _slotIdBase = 9000;
  static const int _maxSlots = 8;

  /// Android needs an explicit channel or notifications arrive with no banner.
  static const AndroidNotificationChannel channel = AndroidNotificationChannel(
    'pai_nudges',
    'PAI nudges',
    description: 'Proactive reminders from your assistant.',
    importance: Importance.high,
  );

  static bool get supported {
    if (kIsWeb) return false;
    return Platform.isIOS || Platform.isAndroid;
  }

  bool get isInitialised => _initialised;
  bool get isPermitted => _permitted;

  /// Prepare the plugin and the timezone database.
  ///
  /// Called before `runApp`, so it must not throw: a device that refuses
  /// notifications should still get a working chat app.
  Future<void> init() async {
    if (!supported || _initialised) return;
    try {
      // zonedSchedule needs a real timezone database; without this every
      // scheduled date silently resolves against UTC.
      tz_data.initializeTimeZones();
      // Matches LOCAL_TZ in src/context.py — the server sends slot times in
      // this zone and the rules judge "evening" by it.
      tz.setLocalLocation(tz.getLocation('Asia/Kolkata'));

      await _plugin.initialize(
        const InitializationSettings(
          android: AndroidInitializationSettings('@mipmap/ic_launcher'),
          // Permission is requested explicitly below so the prompt appears
          // when the user turns the feature on, not at first launch.
          iOS: DarwinInitializationSettings(
            requestAlertPermission: false,
            requestBadgePermission: false,
            requestSoundPermission: false,
          ),
        ),
      );

      await _plugin
          .resolvePlatformSpecificImplementation<
              AndroidFlutterLocalNotificationsPlugin>()
          ?.createNotificationChannel(channel);

      _initialised = true;
    } catch (e) {
      debugPrint('NotificationService: init failed ($e)');
    }
  }

  /// Ask the OS for permission. Safe to call repeatedly — after the first
  /// answer iOS returns the stored one without prompting again.
  Future<bool> requestPermission() async {
    if (!supported || !_initialised) return false;
    try {
      if (Platform.isIOS) {
        _permitted = await _plugin
                .resolvePlatformSpecificImplementation<
                    IOSFlutterLocalNotificationsPlugin>()
                ?.requestPermissions(alert: true, badge: true, sound: true) ??
            false;
      } else {
        _permitted = await _plugin
                .resolvePlatformSpecificImplementation<
                    AndroidFlutterLocalNotificationsPlugin>()
                ?.requestNotificationsPermission() ??
            false;
      }
    } catch (e) {
      debugPrint('NotificationService: permission request failed ($e)');
      _permitted = false;
    }
    return _permitted;
  }

  NotificationDetails get _details => NotificationDetails(
        android: AndroidNotificationDetails(
          channel.id,
          channel.name,
          channelDescription: channel.description,
          importance: Importance.high,
          priority: Priority.high,
        ),
        iOS: const DarwinNotificationDetails(),
      );

  /// Replace the scheduled plan with [plan].
  ///
  /// Everything previously scheduled is cancelled first, which is how a nudge
  /// that has stopped being true disappears rather than firing anyway.
  /// Returns how many were scheduled.
  Future<int> applyPlan(List<PlannedNotification> plan) async {
    if (!supported || !_initialised) return 0;

    for (var i = 0; i < _maxSlots; i++) {
      await _plugin.cancel(_slotIdBase + i);
    }

    final now = DateTime.now();
    var scheduled = 0;

    for (final item in plan) {
      if (scheduled >= _maxSlots) break;
      // A slot already in the past would fire immediately on some platforms.
      if (!item.at.isAfter(now)) continue;

      try {
        await _plugin.zonedSchedule(
          _slotIdBase + scheduled,
          item.title,
          item.body,
          tz.TZDateTime.from(item.at, tz.local),
          _details,
          uiLocalNotificationDateInterpretation:
              UILocalNotificationDateInterpretation.absoluteTime,
          androidScheduleMode: AndroidScheduleMode.inexactAllowWhileIdle,
          payload: item.kind,
        );
        scheduled++;
      } catch (e) {
        debugPrint('NotificationService: could not schedule ${item.kind} ($e)');
      }
    }

    return scheduled;
  }

  /// Everything currently queued with the OS — used to verify scheduling.
  Future<List<PendingNotificationRequest>> pending() async {
    if (!supported || !_initialised) return const [];
    try {
      return await _plugin.pendingNotificationRequests();
    } catch (_) {
      return const [];
    }
  }

  /// Show one immediately, bypassing the schedule. For the settings test.
  Future<void> showNow(String title, String body, {String? payload}) async {
    if (!supported || !_initialised) return;
    await _plugin.show(
      _slotIdBase - 1,
      title,
      body,
      _details,
      payload: payload,
    );
  }

  Future<void> cancelAll() async {
    if (!supported || !_initialised) return;
    for (var i = 0; i < _maxSlots; i++) {
      await _plugin.cancel(_slotIdBase + i);
    }
  }
}
