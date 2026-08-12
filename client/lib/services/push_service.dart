import 'dart:async';
import 'dart:io' show Platform;

import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/foundation.dart';

import 'api_service.dart';
import 'notification_service.dart';

/// Handles a push that arrives while the app is terminated or backgrounded.
///
/// Must be a top-level function annotated for the AOT compiler: Flutter spins
/// up a separate isolate for it, so anything captured from the app's isolate
/// is unavailable here.
@pragma('vm:entry-point')
Future<void> firebaseBackgroundHandler(RemoteMessage message) async {
  // The system already displays the notification for us. This exists so the
  // isolate has a registered entry point; put background bookkeeping here if
  // it ever needs any.
}

/// Server-driven push via Firebase Cloud Messaging.
///
/// Dormant on this deployment: FCM reaches iPhones through APNs, which needs a
/// paid Apple Developer membership to obtain the `aps-environment` entitlement
/// and an APNs key. Until that exists [init] fails harmlessly and nudges are
/// delivered by [NotificationService] instead — see `docs/notifications.md`.
///
/// Presentation is delegated to [NotificationService] because
/// `FlutterLocalNotificationsPlugin` is a singleton: two owners calling
/// `initialize` would have the last one silently win.
class PushService {
  PushService({required ApiService api, required NotificationService notifications})
      : _api = api,
        _notifications = notifications;

  final ApiService _api;
  final NotificationService _notifications;

  bool _initialised = false;
  String? _registeredToken;
  StreamSubscription<String>? _refreshSub;

  static bool get supported {
    if (kIsWeb) return false;
    return Platform.isIOS || Platform.isAndroid;
  }

  bool get isInitialised => _initialised;

  /// Boot Firebase. Called before `runApp`, so it must not throw: without
  /// Firebase configured this is expected to fail, and the app carries on.
  Future<void> init() async {
    if (!supported || _initialised) return;
    try {
      // Options come from GoogleService-Info.plist, which the platform SDK
      // reads automatically. Absent that file this throws, which is the
      // normal path on a deployment without a paid Apple account.
      await Firebase.initializeApp();
      FirebaseMessaging.onBackgroundMessage(firebaseBackgroundHandler);

      // iOS suppresses notifications while the app is in the foreground
      // unless asked otherwise.
      await FirebaseMessaging.instance
          .setForegroundNotificationPresentationOptions(
        alert: true,
        badge: true,
        sound: true,
      );

      FirebaseMessaging.onMessage.listen(_showForeground);
      _initialised = true;
    } catch (e) {
      debugPrint('PushService: Firebase unavailable, remote push off ($e)');
    }
  }

  /// Ask for permission and hand the resulting token to the backend.
  ///
  /// Safe to call on every sign-in and launch — the server treats
  /// registration as idempotent, and FCM rotates tokens on its own schedule.
  Future<bool> registerWithBackend() async {
    if (!supported || !_initialised) return false;

    try {
      final settings = await FirebaseMessaging.instance.requestPermission();
      if (settings.authorizationStatus == AuthorizationStatus.denied) {
        debugPrint('PushService: notification permission denied');
        return false;
      }

      // On iOS the FCM token is only available once APNs has handed over its
      // own token, which can lag a moment behind the permission grant.
      if (Platform.isIOS) {
        final apns = await FirebaseMessaging.instance.getAPNSToken();
        if (apns == null) {
          debugPrint('PushService: APNs token not ready yet');
          return false;
        }
      }

      final token = await FirebaseMessaging.instance.getToken();
      if (token == null || token.isEmpty) return false;

      await _sendToken(token);
      _refreshSub ??=
          FirebaseMessaging.instance.onTokenRefresh.listen(_sendToken);
      return true;
    } catch (e) {
      debugPrint('PushService: registration failed ($e)');
      return false;
    }
  }

  Future<void> _sendToken(String token) async {
    try {
      await _api.registerDevice(
        token: token,
        platform: Platform.isIOS ? 'ios' : 'android',
      );
      _registeredToken = token;
    } catch (e) {
      // A failure here is recoverable: the next launch re-registers.
      debugPrint('PushService: could not register token ($e)');
    }
  }

  /// Stop delivery to this device. Called on sign-out so a shared or handed-on
  /// phone does not keep receiving someone else's nudges.
  Future<void> unregister() async {
    if (!supported || !_initialised) return;
    final token = _registeredToken;
    _registeredToken = null;
    await _refreshSub?.cancel();
    _refreshSub = null;
    if (token == null) return;
    try {
      await _api.unregisterDevice(token);
      await FirebaseMessaging.instance.deleteToken();
    } catch (e) {
      debugPrint('PushService: could not unregister token ($e)');
    }
  }

  Future<void> _showForeground(RemoteMessage message) async {
    final notification = message.notification;
    if (notification == null) return;
    await _notifications.showNow(
      notification.title ?? 'PAI',
      notification.body ?? '',
      payload: message.data['kind'] as String?,
    );
  }

  void dispose() {
    _refreshSub?.cancel();
  }
}
