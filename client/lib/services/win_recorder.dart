import 'dart:ffi';
import 'dart:io';

import 'package:ffi/ffi.dart';

/// Direct FFI wrapper around Windows MCI (winmm.dll) for audio recording.
///
/// This runs in-process — no PowerShell, no subprocesses, no alias
/// scoping issues. Works on every Windows 10/11 machine.
class WinRecorder {
  static final DynamicLibrary _winmm = DynamicLibrary.open('winmm.dll');

  static final int Function(Pointer<Utf8>, Pointer<Utf8>, int, int)
      _mciSendString = _winmm.lookupFunction<
          Uint32 Function(Pointer<Utf8>, Pointer<Utf8>, Uint32, IntPtr),
          int Function(Pointer<Utf8>, Pointer<Utf8>, int, int)>(
    'mciSendStringA',
  );

  bool _recording = false;
  String? _outputPath;

  bool get isRecording => _recording;

  /// Send an MCI command string. Returns 0 on success.
  static int _mci(String command) {
    final cmd = command.toNativeUtf8();
    try {
      return _mciSendString(cmd, nullptr, 0, 0);
    } finally {
      malloc.free(cmd);
    }
  }

  /// Start recording from the default microphone.
  /// Returns true if recording started successfully.
  bool start() {
    if (_recording) return true;

    final tempDir = Directory.systemTemp;
    _outputPath =
        '${tempDir.path}\\pai_rec_${DateTime.now().millisecondsSinceEpoch}.wav';

    // Open a new waveaudio device
    var err = _mci('open new type waveaudio alias pairec');
    if (err != 0) return false;

    // Set format
    _mci('set pairec time format milliseconds');

    // Start recording
    err = _mci('record pairec');
    if (err != 0) {
      _mci('close pairec');
      return false;
    }

    _recording = true;
    return true;
  }

  /// Stop recording and save to a WAV file.
  /// Returns the file path, or null on failure.
  String? stop() {
    if (!_recording) return null;
    _recording = false;

    _mci('stop pairec');

    final path = _outputPath;
    if (path != null) {
      _mci('save pairec $path');
    }

    _mci('close pairec');

    return path;
  }

  /// Cancel recording without saving.
  void cancel() {
    if (!_recording) return;
    _recording = false;
    _mci('stop pairec');
    _mci('close pairec');

    // Delete partial file
    if (_outputPath != null) {
      try {
        File(_outputPath!).deleteSync();
      } catch (_) {}
    }
  }
}
