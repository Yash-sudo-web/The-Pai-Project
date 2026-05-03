import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

/// TTS via Groq API using the Orpheus v1 model.
/// Sends text → gets WAV audio back → plays via a temp file.
class TtsService {
  String _apiKey = '';
  String _baseUrl = 'https://api.groq.com/openai/v1';
  String _model = 'canopylabs/orpheus-v1-english';
  String _voice = 'diana';
  bool _ready = false;
  Process? _player;

  Future<void> init() async {
    _ready = true;
  }

  void configure({required String groqApiKey, String? baseUrl}) {
    _apiKey = groqApiKey;
    if (baseUrl != null && baseUrl.isNotEmpty) _baseUrl = baseUrl;
  }

  Future<void> speak(String text) async {
    if (!_ready || _apiKey.isEmpty || text.trim().isEmpty) return;
    await stop();

    try {
      final response = await http.post(
        Uri.parse('$_baseUrl/audio/speech'),
        headers: {
          'Authorization': 'Bearer $_apiKey',
          'Content-Type': 'application/json',
        },
        body: jsonEncode({
          'model': _model,
          'voice': _voice,
          'input': text,
          'response_format': 'wav',
        }),
      ).timeout(const Duration(seconds: 30));

      if (response.statusCode != 200) return;

      // Write to temp file and play
      final tempDir = Directory.systemTemp;
      final wavFile = File('${tempDir.path}/pai_tts_${DateTime.now().millisecondsSinceEpoch}.wav');
      await wavFile.writeAsBytes(response.bodyBytes);

      if (Platform.isWindows) {
        // Use PowerShell to play the WAV file
        _player = await Process.start('powershell', [
          '-NoProfile',
          '-NonInteractive',
          '-Command',
          "Add-Type -AssemblyName System.Media; "
              "\$p = New-Object System.Media.SoundPlayer '${wavFile.path}'; "
              "\$p.PlaySync(); "
              "Remove-Item '${wavFile.path}' -ErrorAction SilentlyContinue",
        ]);
      } else if (Platform.isMacOS) {
        _player = await Process.start('afplay', [wavFile.path]);
        _player!.exitCode.then((_) => wavFile.deleteSync());
      }
    } catch (_) {
      // Silently fail — TTS is non-critical
    }
  }

  Future<void> stop() async {
    _player?.kill();
    _player = null;
  }

  Future<void> setRate(double rate) async {
    // Groq API doesn't support rate control — no-op
  }
}
