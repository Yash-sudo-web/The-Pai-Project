import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'app.dart';
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

  runApp(
    MultiProvider(
      providers: [
        ChangeNotifierProvider(
          create: (_) => ChatProvider(
            apiService: apiService,
            sttService: sttService,
            ttsService: ttsService,
            prefs: prefs,
          ),
        ),
      ],
      child: const PaiApp(),
    ),
  );
}
