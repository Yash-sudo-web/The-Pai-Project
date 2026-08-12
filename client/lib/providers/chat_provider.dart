import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../config.dart';
import '../models/chat_message.dart';
import '../services/api_service.dart';
import '../services/message_cache.dart';
import '../services/stt_service.dart';
import '../services/tts_service.dart';
import '../services/wake_word_service.dart';

/// Where the voice pipeline currently is.
///
/// [wakeListening] is the hands-free resting state: Porcupine holds the
/// microphone waiting for the keyword. Everything from [recording] onwards is
/// reached by either the wake word or the mic button, and the two paths differ
/// only in what happens to the transcript — see [_handsFree].
enum VoiceState { idle, wakeListening, recording, transcribing, speaking }

class ChatProvider extends ChangeNotifier {
  ChatProvider({
    required ApiService apiService,
    required SttService sttService,
    required TtsService ttsService,
    required SharedPreferences prefs,
    WakeWordService? wakeWordService,
    Future<void> Function()? onUnauthorized,
  })  : _api = apiService,
        _stt = sttService,
        _tts = ttsService,
        _wake = wakeWordService,
        _prefs = prefs,
        _onUnauthorized = onUnauthorized {
    _ttsEnabled = prefs.getBool('pai_tts_enabled') ?? true;
    _wakeEnabled = prefs.getBool(_wakeEnabledKey) ?? false;
    _cache = MessageCache(prefs);
    // Paint the last known conversation before the network is even touched.
    _messages.addAll(_cache.load());
    _configureVoiceServices();
  }

  static const _wakeEnabledKey = 'pai_wake_word_enabled';

  final ApiService _api;
  final SttService _stt;
  final TtsService _tts;

  /// Null on desktop, where Porcupine has no implementation.
  final WakeWordService? _wake;
  final SharedPreferences _prefs;

  /// Invoked when the server rejects our credential, so the app can sign out.
  final Future<void> Function()? _onUnauthorized;

  late final MessageCache _cache;

  final List<ChatMessage> _messages = [];
  bool _isThinking = false;
  VoiceState _voiceState = VoiceState.idle;
  String? _transcribedText; // text to put into the input box
  late bool _ttsEnabled;
  String? _pendingConfirmationId;
  String? _statusError;
  bool _isOnline = true;
  String? _activeTool;
  bool _isStreaming = false;

  late bool _wakeEnabled;

  /// True for the duration of a wake-word turn.
  ///
  /// It is what makes the two voice paths differ: hands-free auto-submits the
  /// transcript and speaks the reply itself so it knows when to reopen the
  /// microphone, while the mic button drops the transcript into the input box
  /// and lets [sendCommand] speak in the ordinary fire-and-forget way.
  bool _handsFree = false;

  /// The assistant's last reply text, captured during a hands-free turn so the
  /// loop can speak it and wait.
  String? _lastReply;

  // ── Getters ───────────────────────────────────────────────────────────────

  List<ChatMessage> get messages => List.unmodifiable(_messages);
  bool get isThinking => _isThinking;
  VoiceState get voiceState => _voiceState;
  bool get isRecording => _voiceState == VoiceState.recording;
  bool get isTranscribing => _voiceState == VoiceState.transcribing;
  bool get isWakeListening => _voiceState == VoiceState.wakeListening;
  bool get isSpeaking => _voiceState == VoiceState.speaking;
  String? get transcribedText => _transcribedText;
  bool get ttsEnabled => _ttsEnabled;
  bool get wakeWordSupported => WakeWordService.supported;
  bool get wakeWordEnabled => _wakeEnabled;
  String get wakeWord => WakeWordService.keywordLabel;
  String? get wakeWordError => _wake?.unavailableReason;
  /// Read live: the credential can change after sign-in, so a snapshot taken
  /// at construction would be stale.
  bool get isConfigured => AppConfig.isConfigured(_prefs);
  String? get statusError => _statusError;
  bool get hasPendingConfirmation => _pendingConfirmationId != null;
  bool get isOnline => _isOnline;

  /// Name of the tool currently executing, for the thinking indicator.
  String? get activeTool => _activeTool;

  /// True while tokens are still arriving for the last assistant message.
  bool get isStreaming => _isStreaming;

  // ── Config ────────────────────────────────────────────────────────────────

  String get currentBaseUrl => AppConfig.getBaseUrl(_prefs);
  String get currentApiKey => AppConfig.getApiKey(_prefs);
  String get currentGroqKey => AppConfig.getGroqApiKey(_prefs);

  void _configureVoiceServices() {
    final groqKey = AppConfig.getGroqApiKey(_prefs);
    if (groqKey.isNotEmpty) {
      _stt.configure(groqApiKey: groqKey);
      _tts.configure(groqApiKey: groqKey);
    }
    unawaited(_applyWakeWordSettings());
  }

  /// Match the detector's running state to the user's preference.
  Future<void> _applyWakeWordSettings() async {
    final wake = _wake;
    if (wake == null) return;

    if (_wakeEnabled) {
      await _resumeWakeListening();
    } else {
      await wake.stop();
      if (_voiceState == VoiceState.wakeListening) _setVoiceState(VoiceState.idle);
    }
  }

  Future<void> saveConfig({
    required String baseUrl,
    required String apiKey,
    required String groqApiKey,
  }) async {
    await AppConfig.saveBaseUrl(_prefs, baseUrl);
    await AppConfig.saveApiKey(_prefs, apiKey);
    await AppConfig.saveGroqApiKey(_prefs, groqApiKey);
    _statusError = null;
    _configureVoiceServices();
    notifyListeners();
  }

  void setTtsEnabled(bool value) {
    _ttsEnabled = value;
    _prefs.setBool('pai_tts_enabled', value);
    if (!value) _tts.stop();
    notifyListeners();
  }

  /// Clear the transcribed text after the UI has consumed it.
  void consumeTranscribedText() {
    _transcribedText = null;
  }

  // ── Send command ──────────────────────────────────────────────────────────

  Future<void> sendCommand(String text) async {
    final trimmed = text.trim();
    if (trimmed.isEmpty || _isThinking) return;

    _messages.add(ChatMessage(
      id: ChatMessage.generateId(),
      role: MessageRole.user,
      text: trimmed,
      timestamp: DateTime.now(),
    ));
    _isThinking = true;
    _statusError = null;
    _activeTool = null;
    notifyListeners();

    // Index of the assistant bubble being streamed into, or -1 before the
    // first token arrives.
    var streamIndex = -1;
    final buffer = StringBuffer();

    try {
      await for (final event in _api.sendCommandStream(trimmed)) {
        _isOnline = true;
        switch (event.type) {
          case CommandEventType.token:
            buffer.write(event.text ?? '');
            if (streamIndex == -1) {
              // First token: stop the spinner and open a bubble to grow.
              _isThinking = false;
              _isStreaming = true;
              _activeTool = null;
              _messages.add(ChatMessage(
                id: ChatMessage.generateId(),
                role: MessageRole.assistant,
                text: buffer.toString(),
                timestamp: DateTime.now(),
              ));
              streamIndex = _messages.length - 1;
            } else {
              _messages[streamIndex] =
                  _messages[streamIndex].copyWith(text: buffer.toString());
            }
            notifyListeners();
            break;

          case CommandEventType.tool:
            _activeTool = event.toolStatus == 'started' ? event.toolName : null;
            notifyListeners();
            break;

          case CommandEventType.pendingConfirmation:
            _pendingConfirmationId = event.pendingId;
            _isThinking = false;
            _addAssistant(
              '⚠️ This action requires your confirmation — use the buttons below.',
            );
            notifyListeners();
            break;

          case CommandEventType.result:
            final result = event.result;
            if (result == null) break;
            if (streamIndex == -1) {
              _addAssistant(result.message, isError: !result.success);
            } else {
              // Reconcile with the authoritative final text.
              _messages[streamIndex] = _messages[streamIndex]
                  .copyWith(text: result.message, isError: !result.success);
              if (result.success) _deliverSpeech(result.message);
            }
            notifyListeners();
            break;

          case CommandEventType.error:
            _addAssistant('Error: ${event.text ?? 'unknown'}', isError: true);
            notifyListeners();
            break;
        }
      }
    } on ApiException catch (e) {
      if (e.statusCode == 401) {
        _statusError = 'Session expired — sign in again.';
        await _onUnauthorized?.call();
      }
      _addAssistant('Error: $e', isError: true);
      _isOnline = false;
    } catch (e) {
      _addAssistant('Network error: $e', isError: true);
      _isOnline = false;
    } finally {
      _isThinking = false;
      _isStreaming = false;
      _activeTool = null;
      notifyListeners();
      await _cache.save(_messages);
    }
  }

  void _addAssistant(String text, {bool isError = false}) {
    _messages.add(ChatMessage(
      id: ChatMessage.generateId(),
      role: MessageRole.assistant,
      text: text,
      timestamp: DateTime.now(),
      isError: isError,
    ));
    if (!isError) _deliverSpeech(text);
  }

  /// Route a reply to the speaker, or to the hands-free loop.
  ///
  /// During a wake-word turn the loop speaks it instead, because it has to
  /// know when playback ends before it can reopen the microphone. Speaking
  /// here as well would talk over it.
  void _deliverSpeech(String text) {
    if (_handsFree) {
      _lastReply = text;
      return;
    }
    if (_ttsEnabled) _tts.speak(text);
  }

  // ── Confirmation ──────────────────────────────────────────────────────────

  Future<void> approveAction() async {
    if (_pendingConfirmationId == null) return;
    final ok = await _api.confirmAction(_pendingConfirmationId!);
    _pendingConfirmationId = null;
    _addAssistant(ok ? 'Action approved and executed.' : 'Approval failed.');
    notifyListeners();
  }

  Future<void> rejectAction() async {
    if (_pendingConfirmationId == null) return;
    await _api.rejectAction(_pendingConfirmationId!);
    _pendingConfirmationId = null;
    _addAssistant('Action rejected.');
    notifyListeners();
  }

  // ── Voice ─────────────────────────────────────────────────────────────────

  /// Toggle recording: start if idle, stop+transcribe if recording.
  ///
  /// Ignored mid-turn: the hands-free loop drives the microphone itself, and a
  /// tap landing in the middle would leave both fighting over it.
  Future<void> toggleRecording() async {
    if (_handsFree) return;

    if (_voiceState == VoiceState.recording) {
      await _stopAndTranscribe();
    } else if (_voiceState == VoiceState.idle ||
        _voiceState == VoiceState.wakeListening) {
      // The detector has to let go of the microphone before we can record.
      await _wake?.stop();
      await _startRecording();
    }
  }

  Future<void> _startRecording() async {
    final ok = await _stt.startRecording();
    if (ok) {
      _voiceState = VoiceState.recording;
      notifyListeners();
    }
  }

  // ── Hands-free (wake word) ────────────────────────────────────────────────

  /// How long to wait for the user to start talking after the wake word.
  static const _promptWindow = Duration(seconds: 3);

  /// How long the microphone stays open after a reply, so a follow-up needs no
  /// second wake word. Closes on its own when nothing is said.
  static const _followUpWindow = Duration(seconds: 5);

  Future<void> setWakeWordEnabled(bool value) async {
    _wakeEnabled = value;
    await _prefs.setBool(_wakeEnabledKey, value);
    notifyListeners();
    await _applyWakeWordSettings();
  }

  /// Re-arm the detector, unless a turn is in progress or the feature is off.
  Future<void> _resumeWakeListening() async {
    final wake = _wake;
    if (wake == null || !_wakeEnabled || _handsFree) return;

    final started = await wake.start(onDetected: _onWakeWordDetected);
    _setVoiceState(started ? VoiceState.wakeListening : VoiceState.idle);
  }

  /// Start a hands-free turn without the wake word.
  ///
  /// The entry point for the iOS Shortcut / Back Tap route: same loop, just a
  /// different trigger. Ignored if a turn is already running.
  Future<void> startHandsFreeTurn() async {
    if (_handsFree) return;
    await _runHandsFreeTurn();
  }

  void _onWakeWordDetected() {
    // Porcupine calls this from its audio callback; never re-enter a turn that
    // is already running.
    if (_handsFree) return;
    unawaited(_runHandsFreeTurn());
  }

  /// One full spoken exchange: listen, ask, speak the answer, offer a
  /// follow-up, and hand the microphone back to the detector at the end.
  Future<void> _runHandsFreeTurn() async {
    _handsFree = true;
    // Porcupine and the recorder cannot hold the microphone at the same time.
    await _wake?.stop();

    try {
      var window = _promptWindow;

      while (true) {
        _setVoiceState(VoiceState.recording);
        final question = await _stt.listenAndTranscribe(
          maxWaitForSpeech: window,
          onSpeechStarted: () => _setVoiceState(VoiceState.recording),
        );

        // Silence: on the first pass they triggered it by accident, on a later
        // pass the conversation is simply over.
        if (question == null || question.trim().isEmpty) break;

        _setVoiceState(VoiceState.transcribing);
        _lastReply = null;
        await sendCommand(question);

        final reply = _lastReply;
        // No reply means the request failed or errored. Opening a follow-up
        // window on that would leave the microphone listening after silence
        // the user cannot explain.
        if (reply == null || reply.trim().isEmpty) break;

        if (_ttsEnabled) {
          _setVoiceState(VoiceState.speaking);
          // Awaited, unlike the typed path: reopening the microphone before
          // playback ends would record the assistant talking to itself.
          await _tts.speakAndWait(reply);
        }

        if (!_wakeEnabled) break; // turned off mid-conversation
        window = _followUpWindow;
      }
    } catch (e) {
      debugPrint('ChatProvider: hands-free turn failed ($e)');
    } finally {
      _handsFree = false;
      _lastReply = null;
      await _resumeWakeListening();
    }
  }

  void _setVoiceState(VoiceState next) {
    if (_voiceState == next) return;
    _voiceState = next;
    notifyListeners();
  }

  Future<void> _stopAndTranscribe() async {
    _voiceState = VoiceState.transcribing;
    notifyListeners();

    final text = await _stt.stopAndTranscribe();

    _voiceState = VoiceState.idle;
    if (text != null && text.trim().isNotEmpty) {
      _transcribedText = text.trim();
    }
    notifyListeners();
    // The recorder has released the microphone, so the detector can have it.
    await _resumeWakeListening();
  }

  void cancelRecording() {
    _stt.cancelRecording();
    _voiceState = VoiceState.idle;
    notifyListeners();
    unawaited(_resumeWakeListening());
  }

  // ── History ───────────────────────────────────────────────────────────────

  /// Fetch list of past sessions (for Chat History panel).
  Future<List<Map<String, dynamic>>> fetchSessions() async {
    try {
      return await _api.fetchSessions();
    } catch (_) {
      return [];
    }
  }

  /// Fetch messages for a specific session (for viewing in history).
  Future<List<ChatMessage>> fetchSessionMessages(String sessionId) async {
    try {
      return await _api.fetchSessionMessages(sessionId);
    } catch (_) {
      return [];
    }
  }

  /// Load a specific session's messages into the chat view.
  Future<int> loadSessionById(String sessionId) async {
    try {
      final messages = await _api.fetchSessionMessages(sessionId);
      if (messages.isEmpty) return 0;
      _messages.clear();
      _messages.addAll(messages);
      notifyListeners();
      await _cache.save(_messages);
      return messages.length;
    } catch (_) {
      return 0;
    }
  }

  Future<int> loadHistory() async {
    try {
      final history = await _api.loadHistory();
      // Keep whatever the cache painted if the server has nothing — losing a
      // visible conversation to an empty response is worse than a stale one.
      if (history.isEmpty) return 0;
      _messages.clear();
      _messages.addAll(history);
      notifyListeners();
      await _cache.save(_messages);
      return history.length;
    } catch (_) {
      return 0;
    }
  }

  Future<void> clearChat() async {
    _messages.clear();
    _tts.stop();
    _pendingConfirmationId = null;
    notifyListeners();
    await _cache.clear();
  }
}
