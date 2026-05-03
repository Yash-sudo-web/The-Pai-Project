import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

/// STT via Groq API (whisper-large-v3-turbo).
///
/// Records audio from the system mic using platform-native tools:
/// - Windows: PowerShell + NAudio-style recording via .NET AudioRecord
/// - macOS: `sox` (install via `brew install sox`)
///
/// Then sends the audio file to Groq's transcription endpoint.
class SttService {
  String _apiKey = '';
  String _baseUrl = 'https://api.groq.com/openai/v1';
  String _model = 'whisper-large-v3-turbo';

  bool _listening = false;
  Process? _recorder;
  String? _tempPath;
  String? _unavailableReason;

  bool get isListening => _listening;
  String? get unavailableReason => _unavailableReason;

  void configure({required String groqApiKey, String? baseUrl}) {
    _apiKey = groqApiKey;
    if (baseUrl != null && baseUrl.isNotEmpty) _baseUrl = baseUrl;
  }

  Future<bool> initialize() async {
    if (_apiKey.isEmpty) {
      _unavailableReason = 'Groq API key not configured — set it in Settings.';
      return false;
    }
    return true;
  }

  /// Start recording from the microphone.
  Future<void> startListening({
    required void Function(String text, bool isFinal) onResult,
  }) async {
    if (_listening || _apiKey.isEmpty) return;
    _listening = true;

    final tempDir = Directory.systemTemp;
    _tempPath =
        '${tempDir.path}/pai_stt_${DateTime.now().millisecondsSinceEpoch}.wav';

    try {
      if (Platform.isWindows) {
        // Record using PowerShell + .NET SoundRecorder (NAudio not needed).
        // Uses Windows.Media.Capture or falls back to ffmpeg if available.
        // Simplest reliable approach: use ffmpeg which ships with many dev machines.
        _recorder = await Process.start('powershell', [
          '-NoProfile',
          '-NonInteractive',
          '-Command',
          // Record via .NET — captures from default mic, writes WAV
          '''
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class WinMM {
    [DllImport("winmm.dll", EntryPoint="mciSendStringA", CharSet=CharSet.Ansi)]
    public static extern int mciSendString(string command, System.Text.StringBuilder buffer, int bufferSize, IntPtr callback);
}
'@
[WinMM]::mciSendString("open new type waveaudio alias mic", \$null, 0, [IntPtr]::Zero)
[WinMM]::mciSendString("set mic time format milliseconds", \$null, 0, [IntPtr]::Zero)
[WinMM]::mciSendString("record mic", \$null, 0, [IntPtr]::Zero)
Write-Host "RECORDING_STARTED"
# Keep recording until process is killed
while (\$true) { Start-Sleep -Milliseconds 100 }
''',
        ]);

        // Wait for recording to actually start
        _recorder!.stdout
            .transform(const SystemEncoding().decoder)
            .listen((line) {});
      } else if (Platform.isMacOS) {
        // macOS: use built-in `sox` or `rec`
        _recorder = await Process.start('sox', [
          '-d',           // default audio device
          '-r', '16000',  // 16kHz sample rate
          '-c', '1',      // mono
          '-b', '16',     // 16-bit
          _tempPath!,
        ]);
      }
    } catch (e) {
      _listening = false;
      _unavailableReason = 'Failed to start recording: $e';
      return;
    }
  }

  /// Stop recording, send audio to Groq, return transcription.
  Future<String?> stopListeningAndTranscribe() async {
    if (!_listening) return null;
    _listening = false;

    try {
      if (Platform.isWindows && _recorder != null) {
        // Send MCI save command before killing
        final saveProc = await Process.run('powershell', [
          '-NoProfile',
          '-NonInteractive',
          '-Command',
          '''
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class WinMM2 {
    [DllImport("winmm.dll", EntryPoint="mciSendStringA", CharSet=CharSet.Ansi)]
    public static extern int mciSendString(string command, System.Text.StringBuilder buffer, int bufferSize, IntPtr callback);
}
'@
[WinMM2]::mciSendString("stop mic", \$null, 0, [IntPtr]::Zero)
[WinMM2]::mciSendString("save mic ${_tempPath!.replaceAll('\\', '\\\\')}", \$null, 0, [IntPtr]::Zero)
[WinMM2]::mciSendString("close mic", \$null, 0, [IntPtr]::Zero)
''',
        ]);
        _recorder!.kill();
        _recorder = null;
      } else {
        // macOS: just kill sox — it flushes the file
        _recorder?.kill(ProcessSignal.sigint);
        await Future.delayed(const Duration(milliseconds: 300));
        _recorder = null;
      }

      // Send to Groq
      final file = File(_tempPath!);
      if (!await file.exists() || await file.length() < 100) {
        return null;
      }

      final text = await _transcribeWithGroq(file);

      // Cleanup
      try {
        await file.delete();
      } catch (_) {}

      return text;
    } catch (e) {
      return null;
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

      final streamed = await request.send().timeout(const Duration(seconds: 30));
      final response = await http.Response.fromStream(streamed);

      if (response.statusCode != 200) return null;

      final data = jsonDecode(response.body) as Map<String, dynamic>;
      final text = (data['text'] as String?)?.trim();
      return (text != null && text.isNotEmpty) ? text : null;
    } catch (_) {
      return null;
    }
  }

  Future<void> cancel() async {
    _recorder?.kill();
    _recorder = null;
    _listening = false;
    // Cleanup temp file
    if (_tempPath != null) {
      try {
        await File(_tempPath!).delete();
      } catch (_) {}
    }
  }
}
