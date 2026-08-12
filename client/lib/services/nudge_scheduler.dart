import 'dart:async';

import 'package:flutter/widgets.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'api_service.dart';
import 'notification_service.dart';

/// Keeps the device's scheduled notifications in step with the server.
///
/// iOS fires a scheduled notification with whatever text it was given, and the
/// app gets no chance to revise it at fire time. So instead of scheduling once,
/// this re-asks the server for the plan on a cadence the server itself chooses
/// — widening to a quarter of an hour when nothing is imminent and tightening
/// so a refresh always lands shortly before a slot. That last refresh is what
/// puts a current number in the notification.
///
/// It only runs while signed in and enabled; both have to be true, because the
/// plan is per-user and polling while signed out only earns 401s.
class NudgeScheduler extends ChangeNotifier {
  NudgeScheduler({
    required ApiService api,
    required NotificationService notifications,
    required SharedPreferences prefs,
  })  : _api = api,
        _notifications = notifications,
        _prefs = prefs {
    _enabled = _prefs.getBool(_enabledKey) ?? false;
    // A refresh on resume matters more than the timer: the app may have been
    // suspended for hours with a stale plan sitting in the OS queue.
    _lifecycle = AppLifecycleListener(
      onResume: () => unawaited(refreshNow()),
    );
  }

  static const _enabledKey = 'pai_nudge_notifications_enabled';

  final ApiService _api;
  final NotificationService _notifications;
  final SharedPreferences _prefs;

  late final AppLifecycleListener _lifecycle;
  Timer? _timer;
  bool _enabled = false;
  bool _signedIn = false;
  bool _refreshing = false;

  DateTime? _lastRefreshAt;
  int _scheduledCount = 0;
  String? _lastError;

  bool get supported => NotificationService.supported;
  bool get enabled => _enabled;
  bool get isRunning => _timer != null;
  DateTime? get lastRefreshAt => _lastRefreshAt;
  int get scheduledCount => _scheduledCount;
  String? get lastError => _lastError;

  /// Turn scheduled nudges on or off. Enabling prompts for permission the
  /// first time, and turning it off clears anything already queued with the OS.
  Future<void> setEnabled(bool value) async {
    _enabled = value;
    await _prefs.setBool(_enabledKey, value);

    if (value) {
      final granted = await _notifications.requestPermission();
      if (!granted) {
        _enabled = false;
        _lastError = 'Notification permission was denied.';
        await _prefs.setBool(_enabledKey, false);
        notifyListeners();
        return;
      }
      _lastError = null;
    }

    notifyListeners();
    await _sync();
  }

  /// Called by the auth layer; the plan is per-user and needs a credential.
  Future<void> setSignedIn(bool value) async {
    if (_signedIn == value) return;
    _signedIn = value;
    await _sync();
  }

  Future<void> _sync() async {
    if (_enabled && _signedIn) {
      await refreshNow();
    } else {
      _timer?.cancel();
      _timer = null;
      _scheduledCount = 0;
      await _notifications.cancelAll();
      notifyListeners();
    }
  }

  /// Fetch the current plan and hand it to the OS.
  Future<void> refreshNow() async {
    if (!_enabled || !_signedIn || _refreshing) return;
    _refreshing = true;

    try {
      final plan = await _api.fetchNudgeSchedule();

      if (!plan.ok) {
        // Could not reach the server. Leave whatever is already scheduled in
        // place — a stale notification beats silently cancelling a real one —
        // and try again on the default cadence.
        _lastError = 'Could not reach the server; keeping the last plan.';
        _scheduleNextRefresh(plan.refreshAfterSeconds);
        return;
      }

      final items = plan.slots
          .map(PlannedNotification.fromJson)
          .whereType<PlannedNotification>()
          .toList();

      _scheduledCount = await _notifications.applyPlan(items);
      _lastRefreshAt = DateTime.now();
      _lastError = null;
      _scheduleNextRefresh(plan.refreshAfterSeconds);
    } on ApiException catch (e) {
      if (e.statusCode == 401) {
        // The session died; stop until the auth layer tells us otherwise.
        _signedIn = false;
        _timer?.cancel();
        _timer = null;
      }
      _lastError = e.message;
    } catch (e) {
      _lastError = 'Could not refresh notifications: $e';
    } finally {
      _refreshing = false;
      notifyListeners();
    }
  }

  void _scheduleNextRefresh(int seconds) {
    _timer?.cancel();
    _timer = Timer(
      Duration(seconds: seconds.clamp(60, 3600)),
      () => unawaited(refreshNow()),
    );
  }

  /// Show a notification immediately, to verify the path end to end.
  Future<void> sendTestNotification() async {
    await _notifications.requestPermission();
    await _notifications.showNow(
      'PAI',
      'Test notification — scheduled nudges are working.',
      payload: 'test',
    );
  }

  /// How many notifications the OS is currently holding for us.
  Future<int> pendingCount() async => (await _notifications.pending()).length;

  @override
  void dispose() {
    _timer?.cancel();
    _lifecycle.dispose();
    super.dispose();
  }
}
