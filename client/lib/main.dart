import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'app.dart';
import 'providers/auth_provider.dart';
import 'providers/chat_provider.dart';
import 'services/api_service.dart';
import 'services/stt_service.dart';
import 'services/tts_service.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  final prefs = await SharedPreferences.getInstance();
  final apiService = ApiService(prefs: prefs);
  final sttService = SttService();
  final ttsService = TtsService();
  await ttsService.init();

  // Built eagerly so the chat provider can hand 401s back to the auth
  // provider, which drops the dead session and returns to the login screen.
  final authProvider = AuthProvider(api: apiService, prefs: prefs);
  final chatProvider = ChatProvider(
    apiService: apiService,
    sttService: sttService,
    ttsService: ttsService,
    prefs: prefs,
    onUnauthorized: authProvider.onUnauthorized,
  );

  runApp(
    MultiProvider(
      providers: [
        ChangeNotifierProvider.value(value: authProvider),
        ChangeNotifierProvider.value(value: chatProvider),
      ],
      child: const PaiApp(),
    ),
  );
}
