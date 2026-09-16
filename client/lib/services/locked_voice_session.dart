import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

/// iOS audio-session and Live Activity lifecycle for one user-started turn.
/// Other platforms keep using their existing voice path.
class LockedVoiceSession {
  static const _channel = MethodChannel('pai/locked_voice');

  static Future<void> _call(String method, [Object? arguments]) async {
    if (!Platform.isIOS) return;
    try {
      await _channel.invokeMethod<void>(method, arguments);
    } on PlatformException catch (e) {
      debugPrint('LockedVoiceSession.$method: ${e.message}');
    } on MissingPluginException {
      debugPrint('LockedVoiceSession.$method: iOS bridge unavailable');
    }
  }

  /// Returns the native Live Activity result, including a failure reason.
  /// Audio recording can still proceed when the activity is unavailable.
  static Future<String?> begin() async {
    if (!Platform.isIOS) return null;
    try {
      return await _channel.invokeMethod<String>('begin');
    } on PlatformException catch (e) {
      return 'iOS voice session failed: ${e.message ?? e.code}';
    } on MissingPluginException {
      return 'iOS voice bridge is missing from this app build.';
    }
  }

  /// Called before recording stops, so iOS does not suspend the app during the
  /// transcription, assistant request, or speech synthesis network gap.
  static Future<void> beginProcessing() => _call('beginProcessing');

  static Future<void> endProcessing() => _call('endProcessing');

  static Future<void> phase(String value) => _call('phase', value);

  static Future<void> end({bool showCompletion = false}) =>
      _call('end', showCompletion);
}
