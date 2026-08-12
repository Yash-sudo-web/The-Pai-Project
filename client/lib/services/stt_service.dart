import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
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

  /// Record one spoken utterance, ending it automatically, then transcribe.
  ///
  /// Used by the hands-free path, where there is no second tap to mark the
  /// end of speech. Returns null when the user never spoke, or when the audio
  /// could not be transcribed.
  ///
  /// The two phases behave differently on purpose. Before speech starts we are
  /// willing to wait [maxWaitForSpeech] and then give up entirely — that is
  /// what makes a follow-up window close on its own when the user has nothing
  /// more to say. Once speech has started, [silenceWindow] of quiet ends the
  /// utterance and [maxDuration] caps a monologue.
  ///
  /// Levels are dBFS, so they are negative and 0.0 is the loudest possible
  /// sample; -35 sits comfortably between room tone and a speaking voice.
  ///
  /// Mobile only — endpointing reads the `record` package's amplitude stream,
  /// which the Windows FFI recorder does not provide.
  Future<String?> listenAndTranscribe({
    Duration maxWaitForSpeech = const Duration(seconds: 3),
    Duration silenceWindow = const Duration(milliseconds: 1500),
    Duration maxDuration = const Duration(seconds: 15),
    double speechThresholdDb = -35.0,
    VoidCallback? onSpeechStarted,
  }) async {
    if (Platform.isWindows) {
      _unavailableReason = 'Hands-free listening is not available on Windows.';
      return null;
    }
    if (!await startRecording()) return null;

    const tick = Duration(milliseconds: 150);
    final startedAt = DateTime.now();
    DateTime? speechStartedAt;
    DateTime? lastLoudAt;
    var heardSpeech = false;

    final completer = Completer<bool>();
    late final StreamSubscription<Amplitude> sub;

    void finish(bool spoke) {
      if (!completer.isCompleted) completer.complete(spoke);
    }

    sub = _mobileRecorder.onAmplitudeChanged(tick).listen((amplitude) {
      final now = DateTime.now();
      final loud = amplitude.current > speechThresholdDb;

      if (loud) {
        lastLoudAt = now;
        if (!heardSpeech) {
          heardSpeech = true;
          speechStartedAt = now;
          onSpeechStarted?.call();
        }
      }

      if (!heardSpeech) {
        // Still waiting for them to begin.
        if (now.difference(startedAt) >= maxWaitForSpeech) finish(false);
        return;
      }

      if (now.difference(speechStartedAt!) >= maxDuration) {
        finish(true);
      } else if (lastLoudAt != null &&
          now.difference(lastLoudAt!) >= silenceWindow) {
        finish(true);
      }
    }, onError: (_) => finish(heardSpeech));

    // Backstop: if amplitude events stop arriving the stream listener above
    // can never fire again, and without this the turn would hang forever.
    final guard = Timer(
      maxWaitForSpeech + maxDuration + const Duration(seconds: 2),
      () => finish(heardSpeech),
    );

    final spoke = await completer.future;
    await sub.cancel();
    guard.cancel();

    if (!spoke) {
      cancelRecording();
      return null;
    }
    return stopAndTranscribe();
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
