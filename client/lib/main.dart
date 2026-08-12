import 'dart:async';

import 'package:app_links/app_links.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'app.dart';
import 'providers/auth_provider.dart';
import 'providers/chat_provider.dart';
import 'services/api_service.dart';
import 'services/notification_service.dart';
import 'services/nudge_scheduler.dart';
import 'services/push_service.dart';
import 'services/stt_service.dart';
import 'services/tts_service.dart';
import 'services/wake_word_service.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  final prefs = await SharedPreferences.getInstance();
  final apiService = ApiService(prefs: prefs);
  final sttService = SttService();
  final ttsService = TtsService();
  await ttsService.init();

  // Owns the local-notification plugin, which PushService also presents
  // through. init() no-ops on unsupported platforms and swallows its own
  // failures, so neither can stop the app booting.
  final notificationService = NotificationService();
  await notificationService.init();

  final nudgeScheduler = NudgeScheduler(
    api: apiService,
    notifications: notificationService,
    prefs: prefs,
  );

  // Dormant without a paid Apple account; see docs/notifications.md.
  final pushService = PushService.supported
      ? PushService(api: apiService, notifications: notificationService)
      : null;
  await pushService?.init();

  // Built eagerly so the chat provider can hand 401s back to the auth
  // provider, which drops the dead session and returns to the login screen.
  final authProvider = AuthProvider(
    api: apiService,
    prefs: prefs,
    push: pushService,
    nudges: nudgeScheduler,
  );
  // Null on desktop — Porcupine has no Windows implementation.
  final wakeWordService =
      WakeWordService.supported ? WakeWordService() : null;

  final chatProvider = ChatProvider(
    apiService: apiService,
    sttService: sttService,
    ttsService: ttsService,
    wakeWordService: wakeWordService,
    prefs: prefs,
    onUnauthorized: authProvider.onUnauthorized,
  );

  // Back Tap / Shortcuts route: iOS cannot bind a gesture to an app action
  // directly, so a Shortcut opens `pai://listen` and that starts a turn. Costs
  // no battery and works when wake-word detection is off or has died.
  unawaited(_listenForShortcutLaunch(chatProvider));

  runApp(
    MultiProvider(
      providers: [
        ChangeNotifierProvider.value(value: authProvider),
        ChangeNotifierProvider.value(value: chatProvider),
        ChangeNotifierProvider.value(value: nudgeScheduler),
      ],
      child: const PaiApp(),
    ),
  );
}

/// Route incoming `pai://listen` links to a hands-free turn.
Future<void> _listenForShortcutLaunch(ChatProvider chat) async {
  bool isListenLink(Uri uri) => uri.scheme == 'pai' && uri.host == 'listen';

  try {
    final links = AppLinks();
    // A cold start arrives here rather than on the stream.
    final initial = await links.getInitialLink();
    if (initial != null && isListenLink(initial)) {
      unawaited(chat.startHandsFreeTurn());
    }
    links.uriLinkStream.listen((uri) {
      if (isListenLink(uri)) unawaited(chat.startHandsFreeTurn());
    });
  } catch (e) {
    debugPrint('main: deep-link listener unavailable ($e)');
  }
}
