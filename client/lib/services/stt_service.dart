import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;

import 'win_recorder.dart';

/// STT via Groq Whisper API (whisper-large-v3-turbo).
///
/// Records audio using [WinRecorder] (direct FFI to winmm.dll),
/// then sends the WAV file to Groq for transcription.
class SttService {
  String _apiKey = '';
  String _baseUrl = 'https://api.groq.com/openai/v1';
  String _model = 'whisper-large-v3-turbo';

  final WinRecorder _recorder = WinRecorder();
  String? _unavailableReason;

  bool get isListening => _recorder.isRecording;
  String? get unavailableReason => _unavailableReason;

  void configure({required String groqApiKey, String? baseUrl}) {
    _apiKey = groqApiKey;
    if (baseUrl != null && baseUrl.isNotEmpty) _baseUrl = baseUrl;
    _unavailableReason = null;
  }

  Future<bool> initialize() async {
    if (_apiKey.isEmpty) {
      _unavailableReason = 'Groq API key not configured — set it in Settings.';
      return false;
    }
    if (!Platform.isWindows) {
      _unavailableReason = 'Voice recording requires Windows.';
      return false;
    }
    return true;
  }

  /// Start recording from the microphone.
  bool startRecording() {
    if (_apiKey.isEmpty || !Platform.isWindows) return false;
    final ok = _recorder.start();
    if (!ok) {
      _unavailableReason = 'Failed to open microphone.';
    }
    return ok;
  }

  /// Stop recording and transcribe via Groq Whisper.
  /// Returns the transcribed text, or null on failure.
  Future<String?> stopAndTranscribe() async {
    final path = _recorder.stop();
    if (path == null) return null;

    final file = File(path);
    if (!await file.exists()) return null;

    final size = await file.length();
    if (size < 1000) {
      // Too small — silence or error
      try { await file.delete(); } catch (_) {}
      return null;
    }

    try {
      final text = await _transcribeWithGroq(file);
      return text;
    } finally {
      try { await file.delete(); } catch (_) {}
    }
  }

  Future<String?> _transcribeWithGroq(File audioFile) async {
    try {
      final request = http.MultipartRequest(
        'POST',
        Uri.parse('$_baseUrl/audio/transcriptions'),
      );
      request.headers['Authorization'] = 'Bearer $_apiKey';
      request.fields['model'] = _model;
      request.fields['response_format'] = 'json';
      request.fields['temperature'] = '0.0';
      request.files.add(await http.MultipartFile.fromPath(
        'file',
        audioFile.path,
        filename: 'command.wav',
      ));

      final streamed =
          await request.send().timeout(const Duration(seconds: 30));
      final response = await http.Response.fromStream(streamed);

      if (response.statusCode != 200) return null;

      final data = jsonDecode(response.body) as Map<String, dynamic>;
      final text = (data['text'] as String?)?.trim();
      return (text != null && text.isNotEmpty) ? text : null;
    } catch (_) {
      return null;
    }
  }

  void cancelRecording() {
    _recorder.cancel();
  }

  void dispose() {
    if (_recorder.isRecording) _recorder.cancel();
  }
}
