import 'package:speech_to_text/speech_recognition_result.dart';
import 'package:speech_to_text/speech_to_text.dart';

class SttService {
  final _stt = SpeechToText();
  bool _initialized = false;
  bool _listening = false;
  String? _unavailableReason;

  bool get isListening => _listening;
  /// null means available (or not yet tried). Non-null = reason STT is disabled.
  String? get unavailableReason => _unavailableReason;

  Future<bool> initialize() async {
    if (_initialized) return true;
    try {
      _initialized = await _stt.initialize(
        onError: (_) => _listening = false,
      );
      if (!_initialized) {
        _unavailableReason = 'Speech Recognition not available on this device.';
      }
    } catch (e) {
      // MissingPluginException on Windows when Speech Recognition isn't enabled,
      // or any other platform error — degrade gracefully.
      _initialized = false;
      _unavailableReason =
          'Enable Windows Speech Recognition in Settings → Privacy → Speech.';
    }
    return _initialized;
  }

  Future<void> startListening({
    required void Function(String text, bool isFinal) onResult,
  }) async {
    if (!_initialized) await initialize();
    if (!_initialized || _listening) return;
    _listening = true;

    try {
      await _stt.listen(
        onResult: (SpeechRecognitionResult result) {
          onResult(result.recognizedWords, result.finalResult);
          if (result.finalResult) _listening = false;
        },
        listenFor: const Duration(seconds: 30),
        pauseFor: const Duration(seconds: 2),
        localeId: 'en_US',
      );
    } catch (_) {
      _listening = false;
    }
  }

  Future<void> stopListening() async {
    if (!_listening) return;
    try {
      await _stt.stop();
    } catch (_) {}
    _listening = false;
  }

  Future<void> cancel() async {
    try {
      await _stt.cancel();
    } catch (_) {}
    _listening = false;
  }
}
