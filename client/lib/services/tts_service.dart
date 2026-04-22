import 'package:flutter_tts/flutter_tts.dart';

class TtsService {
  final _tts = FlutterTts();
  bool _ready = false;

  Future<void> init() async {
    if (_ready) return;
    await _tts.setLanguage('en-US');
    await _tts.setSpeechRate(0.52);
    await _tts.setVolume(1.0);
    await _tts.setPitch(1.0);
    _ready = true;
  }

  Future<void> speak(String text) async {
    if (!_ready) await init();
    await _tts.stop();
    await _tts.speak(text);
  }

  Future<void> stop() => _tts.stop();

  Future<void> setRate(double rate) =>
      _tts.setSpeechRate(rate.clamp(0.1, 1.0));
}
