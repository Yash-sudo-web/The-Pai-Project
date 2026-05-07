import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;
import 'package:record/record.dart';

import 'win_recorder.dart';

/// STT via Groq Whisper API (whisper-large-v3-turbo).
///
/// Records audio using [WinRecorder] on Windows (direct FFI to winmm.dll),
/// and the `record` package on iOS/Android, then sends the WAV to Groq.
class SttService {
  String _apiKey = '';
  String _baseUrl = 'https://api.groq.com/openai/v1';
  String _model = 'whisper-large-v3-turbo';

  final WinRecorder _winRecorder = WinRecorder();
  final AudioRecorder _mobileRecorder = AudioRecorder();
  String? _mobilePath;
  bool _isMobileRecording = false;

  String? _unavailableReason;

  bool get isListening => Platform.isWindows ? _winRecorder.isRecording : _isMobileRecording;
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
    if (!Platform.isWindows && !Platform.isIOS && !Platform.isAndroid) {
      _unavailableReason = 'Voice recording requires Windows, iOS, or Android.';
      return false;
    }
    
    // Request permission on mobile
    if (Platform.isIOS || Platform.isAndroid) {
      if (!await _mobileRecorder.hasPermission()) {
        _unavailableReason = 'Microphone permission denied.';
        return false;
      }
    }
    return true;
  }

  /// Start recording from the microphone.
  Future<bool> startRecording() async {
    if (_apiKey.isEmpty) return false;
    
    if (Platform.isWindows) {
      final ok = _winRecorder.start();
      if (!ok) _unavailableReason = 'Failed to open microphone on Windows.';
      return ok;
    } else {
      try {
        final tempDir = Directory.systemTemp;
        _mobilePath = '${tempDir.path}/pai_mobile_${DateTime.now().millisecondsSinceEpoch}.wav';
        await _mobileRecorder.start(
          const RecordConfig(encoder: AudioEncoder.wav),
          path: _mobilePath!,
        );
        _isMobileRecording = true;
        return true;
      } catch (e) {
        _unavailableReason = 'Failed to start mobile recording: $e';
        return false;
      }
    }
  }

  /// Stop recording and transcribe via Groq Whisper.
  /// Returns the transcribed text, or null on failure.
  Future<String?> stopAndTranscribe() async {
    String? path;
    
    if (Platform.isWindows) {
      path = _winRecorder.stop();
    } else {
      path = await _mobileRecorder.stop();
      _isMobileRecording = false;
    }

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

  void cancelRecording() async {
    if (Platform.isWindows) {
      _winRecorder.cancel();
    } else {
      await _mobileRecorder.stop();
      _isMobileRecording = false;
      if (_mobilePath != null) {
        try { await File(_mobilePath!).delete(); } catch (_) {}
      }
    }
  }

  void dispose() {
    cancelRecording();
    _mobileRecorder.dispose();
  }
}
