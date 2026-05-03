import 'dart:convert';
import 'dart:io';

import 'package:audioplayers/audioplayers.dart';
import 'package:http/http.dart' as http;

/// TTS via Groq API (Orpheus v1 English model).
///
/// Sends text → Groq returns WAV audio → plays via `audioplayers`.
class TtsService {
  String _apiKey = '';
  String _baseUrl = 'https://api.groq.com/openai/v1';
  String _model = 'canopylabs/orpheus-v1-english';
  String _voice = 'diana';
  bool _ready = false;

  final AudioPlayer _player = AudioPlayer();

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

      // Write to temp file and play via audioplayers
      final tempDir = Directory.systemTemp;
      final wavFile = File(
          '${tempDir.path}/pai_tts_${DateTime.now().millisecondsSinceEpoch}.wav');
      await wavFile.writeAsBytes(response.bodyBytes);

      await _player.play(DeviceFileSource(wavFile.path));

      // Cleanup after playback finishes
      _player.onPlayerComplete.first.then((_) {
        try {
          wavFile.deleteSync();
        } catch (_) {}
      });
    } catch (_) {
      // Silently fail — TTS is non-critical
    }
  }

  Future<void> stop() async {
    try {
      await _player.stop();
    } catch (_) {}
  }

  Future<void> setRate(double rate) async {
    // Map 0.0-1.0 → playback rate 0.5-2.0
    final playbackRate = 0.5 + (rate * 1.5);
    await _player.setPlaybackRate(playbackRate);
  }

  void dispose() {
    _player.dispose();
  }
}
