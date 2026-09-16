import 'dart:async';
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

  /// The temp WAV currently playing, deleted once playback ends.
  File? _currentFile;
  Completer<void>? _activeWait;
  int _playbackGeneration = 0;

  Future<void> init() async {
    if (Platform.isIOS) {
      // Keep the same input/output category as the native locked-turn session.
      // audioplayers defaults to .playback, which would replace the recording
      // session's category when a spoken reply starts.
      await _player.setAudioContext(AudioContext(
        iOS: AudioContextIOS(
          category: AVAudioSessionCategory.playAndRecord,
          options: const {
            AVAudioSessionOptions.defaultToSpeaker,
            AVAudioSessionOptions.allowBluetooth,
          },
        ),
      ));
    }
    _ready = true;
  }

  void configure({required String groqApiKey, String? baseUrl}) {
    _apiKey = groqApiKey;
    if (baseUrl != null && baseUrl.isNotEmpty) _baseUrl = baseUrl;
  }

  Future<void> speak(String text) async {
    final started = await _startPlayback(text);
    if (!started) return;

    // Cleanup after playback finishes
    unawaited(_player.onPlayerComplete.first.then((_) => _cleanup()));
  }

  /// Speak, and complete only once playback has actually finished.
  ///
  /// The hands-free loop needs this: it has to reopen the microphone the
  /// moment the reply ends, and no sooner — otherwise it records the assistant
  /// talking over itself.
  ///
  /// Returns as soon as it can when there is nothing to play, and is capped by
  /// a timeout so a playback event that never arrives cannot strand the caller
  /// with a dead microphone.
  Future<void> speakAndWait(String text) async {
    // Subscribed before playback starts, not after: a short reply can finish
    // before a listener attached afterwards ever sees the event, and the
    // caller would then sit on the timeout with the microphone shut.
    final done = Completer<void>();
    final sub = _player.onPlayerComplete.listen(
      (_) {
        if (!done.isCompleted) done.complete();
      },
      onError: (_) {
        if (!done.isCompleted) done.complete();
      },
    );

    try {
      if (!await _startPlayback(text)) return;
      _activeWait = done;
      await done.future.timeout(const Duration(minutes: 2));
    } catch (_) {
      // Timed out or errored — fall through and let the caller carry on.
    } finally {
      if (identical(_activeWait, done)) _activeWait = null;
      await sub.cancel();
      _cleanup();
    }
  }

  /// Synthesise [text] and begin playing it. Returns whether playback started.
  Future<bool> _startPlayback(String text) async {
    if (!_ready || _apiKey.isEmpty || text.trim().isEmpty) return false;
    await stop();
    final generation = _playbackGeneration;

    try {
      final response = await http
          .post(
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
          )
          .timeout(const Duration(seconds: 30));

      if (response.statusCode != 200) return false;
      if (generation != _playbackGeneration) return false;

      // Write to temp file and play via audioplayers
      final tempDir = Directory.systemTemp;
      final wavFile = File(
          '${tempDir.path}/pai_tts_${DateTime.now().millisecondsSinceEpoch}.wav');
      await wavFile.writeAsBytes(response.bodyBytes);
      if (generation != _playbackGeneration) {
        await wavFile.delete();
        return false;
      }
      _currentFile = wavFile;

      await _player.play(DeviceFileSource(wavFile.path));
      return generation == _playbackGeneration;
    } catch (_) {
      // Silently fail — TTS is non-critical
      return false;
    }
  }

  void _cleanup() {
    final file = _currentFile;
    _currentFile = null;
    if (file == null) return;
    try {
      file.deleteSync();
    } catch (_) {}
  }

  Future<void> stop() async {
    _playbackGeneration++;
    final wait = _activeWait;
    if (wait != null && !wait.isCompleted) wait.complete();
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
