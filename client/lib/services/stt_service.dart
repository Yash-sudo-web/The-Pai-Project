import 'package:speech_to_text/speech_recognition_result.dart';
import 'package:speech_to_text/speech_to_text.dart';

class SttService {
  final _stt = SpeechToText();
  bool _initialized = false;
  bool _listening = false;

  bool get isListening => _listening;

  Future<bool> initialize() async {
    if (_initialized) return true;
    _initialized = await _stt.initialize(
      onError: (_) => _listening = false,
    );
    return _initialized;
  }

  Future<bool> get isAvailable async {
    if (!_initialized) await initialize();
    return _initialized;
  }

  Future<void> startListening({
    required void Function(String text, bool isFinal) onResult,
  }) async {
    if (!_initialized) await initialize();
    if (!_initialized || _listening) return;
    _listening = true;

    await _stt.listen(
      onResult: (SpeechRecognitionResult result) {
        onResult(result.recognizedWords, result.finalResult);
        if (result.finalResult) _listening = false;
      },
      listenFor: const Duration(seconds: 30),
      pauseFor: const Duration(seconds: 2),
      localeId: 'en_US',
    );
  }

  Future<void> stopListening() async {
    if (!_listening) return;
    await _stt.stop();
    _listening = false;
  }

  Future<void> cancel() async {
    await _stt.cancel();
    _listening = false;
  }
}
