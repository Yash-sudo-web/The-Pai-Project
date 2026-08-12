import 'dart:async';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart' show rootBundle;
import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';
import 'package:sherpa_onnx/sherpa_onnx.dart' as sherpa;

/// Always-on wake-word detection using sherpa-onnx keyword spotting.
///
/// The model is a small streaming zipformer transducer (Apache-2.0, bundled in
/// `assets/kws/`) with decoding constrained to a keyword list, so the phrase is
/// plain text rather than a trained model — `assets/kws/keywords.txt` holds it
/// as BPE tokens. See `docs/wake-word.md` for how to change it.
///
/// This holds the microphone while it runs and so does `SttService`; the two
/// can never be active at once, which is why `ChatProvider` stops this before
/// recording a question and starts it again afterwards.
class WakeWordService {
  static const String keywordLabel = 'Hey Pai';

  /// The model is trained at 16 kHz; feeding it anything else quietly ruins
  /// detection rather than failing.
  static const int _sampleRate = 16000;

  /// Detection sensitivity. Lower the threshold to catch the phrase more often
  /// at the cost of more false triggers. Both are worth tuning against a real
  /// voice in a real room — see `docs/wake-word.md`.
  static const double _keywordsScore = 1.0;
  static const double _keywordsThreshold = 0.25;

  static const List<String> _modelAssets = [
    'encoder.int8.onnx',
    'decoder.int8.onnx',
    'joiner.int8.onnx',
    'tokens.txt',
    'keywords.txt',
  ];

  static bool _bindingsReady = false;

  final AudioRecorder _recorder = AudioRecorder();

  sherpa.KeywordSpotter? _spotter;
  sherpa.OnlineStream? _stream;
  StreamSubscription<Uint8List>? _audioSub;

  bool _listening = false;
  String? _unavailableReason;
  VoidCallback? _onDetected;

  /// sherpa-onnx ships iOS and Android binaries; the desktop build has none
  /// wired up here.
  static bool get supported {
    if (kIsWeb) return false;
    return Platform.isIOS || Platform.isAndroid;
  }

  bool get isListening => _listening;
  String? get unavailableReason => _unavailableReason;

  /// Begin listening. [onDetected] fires each time the phrase is heard.
  /// Returns false on failure, with [unavailableReason] explaining why.
  Future<bool> start({required VoidCallback onDetected}) async {
    if (!supported) {
      _unavailableReason = 'Wake word requires iOS or Android.';
      return false;
    }
    if (_listening) return true;

    _onDetected = onDetected;

    try {
      if (!await _prepare()) return false;

      if (!await _recorder.hasPermission()) {
        _unavailableReason = 'Microphone permission denied.';
        return false;
      }

      final audio = await _recorder.startStream(
        const RecordConfig(
          encoder: AudioEncoder.pcm16bits,
          sampleRate: _sampleRate,
          numChannels: 1,
        ),
      );

      _audioSub = audio.listen(
        _onAudioChunk,
        onError: (Object e) {
          // Typically the audio session being taken by a call or another app.
          _unavailableReason = 'Wake-word detection stopped: $e';
          _listening = false;
        },
        cancelOnError: true,
      );

      _listening = true;
      _unavailableReason = null;
      return true;
    } catch (e) {
      _unavailableReason = 'Could not start wake-word detection: $e';
      await _teardownAudio();
      return false;
    }
  }

  /// Stop listening and release the microphone, keeping the model loaded so
  /// the next [start] costs nothing but reopening the audio stream.
  Future<void> stop() async {
    if (!_listening) return;
    _listening = false;
    await _teardownAudio();

    // Drop whatever was mid-phrase, so audio captured before the pause cannot
    // trigger a detection after it resumes.
    final spotter = _spotter;
    final stream = _stream;
    if (spotter != null && stream != null) spotter.reset(stream);
  }

  // ------------------------------------------------------------------
  // Internals
  // ------------------------------------------------------------------

  /// Load the native bindings and build the spotter. Idempotent.
  Future<bool> _prepare() async {
    if (_spotter != null && _stream != null) return true;

    if (!_bindingsReady) {
      sherpa.initBindings();
      _bindingsReady = true;
    }

    final dir = await _stageModel();
    if (dir == null) return false;

    final config = sherpa.KeywordSpotterConfig(
      model: sherpa.OnlineModelConfig(
        transducer: sherpa.OnlineTransducerModelConfig(
          encoder: '$dir/encoder.int8.onnx',
          decoder: '$dir/decoder.int8.onnx',
          joiner: '$dir/joiner.int8.onnx',
        ),
        tokens: '$dir/tokens.txt',
        modelType: 'zipformer2',
        // Quiet by default; the engine is otherwise chatty on every frame.
        debug: false,
      ),
      keywordsFile: '$dir/keywords.txt',
      keywordsScore: _keywordsScore,
      keywordsThreshold: _keywordsThreshold,
    );

    _spotter = sherpa.KeywordSpotter(config);
    _stream = _spotter!.createStream();
    return true;
  }

  /// Copy the bundled model out of the asset archive onto the filesystem.
  ///
  /// sherpa-onnx is native code that opens files by path, so the assets cannot
  /// be handed to it from the bundle. Copied once, then reused.
  Future<String?> _stageModel() async {
    try {
      final support = await getApplicationSupportDirectory();
      final dir = Directory('${support.path}/kws');
      if (!await dir.exists()) await dir.create(recursive: true);

      for (final name in _modelAssets) {
        final file = File('${dir.path}/$name');
        if (await file.exists() && await file.length() > 0) continue;
        final data = await rootBundle.load('assets/kws/$name');
        await file.writeAsBytes(
          data.buffer.asUint8List(data.offsetInBytes, data.lengthInBytes),
          flush: true,
        );
      }
      return dir.path;
    } catch (e) {
      _unavailableReason = 'Could not unpack the wake-word model: $e';
      return null;
    }
  }

  void _onAudioChunk(Uint8List bytes) {
    final spotter = _spotter;
    final stream = _stream;
    if (spotter == null || stream == null || !_listening) return;

    try {
      stream.acceptWaveform(
        samples: _pcm16ToFloat32(bytes),
        sampleRate: _sampleRate,
      );
      while (spotter.isReady(stream)) {
        spotter.decode(stream);
      }
      if (spotter.getResult(stream).keyword.isNotEmpty) {
        // Reset before notifying: the callback synchronously stops this
        // service, and a stream still holding the match would fire again the
        // moment it restarts.
        spotter.reset(stream);
        _onDetected?.call();
      }
    } catch (e) {
      debugPrint('WakeWordService: decode failed ($e)');
    }
  }

  /// `record` yields little-endian signed 16-bit PCM; the model wants floats
  /// normalised to [-1, 1].
  static Float32List _pcm16ToFloat32(Uint8List bytes) {
    final view = ByteData.sublistView(bytes);
    final count = bytes.lengthInBytes ~/ 2;
    final samples = Float32List(count);
    for (var i = 0; i < count; i++) {
      samples[i] = view.getInt16(i * 2, Endian.little) / 32768.0;
    }
    return samples;
  }

  Future<void> _teardownAudio() async {
    await _audioSub?.cancel();
    _audioSub = null;
    try {
      if (await _recorder.isRecording()) await _recorder.stop();
    } catch (_) {
      // Already stopped.
    }
  }

  Future<void> dispose() async {
    await stop();
    _stream?.free();
    _stream = null;
    _spotter?.free();
    _spotter = null;
    await _recorder.dispose();
  }
}
