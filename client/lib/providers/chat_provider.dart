import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../config.dart';
import '../models/chat_message.dart';
import '../services/api_service.dart';
import '../services/message_cache.dart';
import '../services/stt_service.dart';
import '../services/tts_service.dart';

enum VoiceState { idle, recording, transcribing }

class ChatProvider extends ChangeNotifier {
  ChatProvider({
    required ApiService apiService,
    required SttService sttService,
    required TtsService ttsService,
    required SharedPreferences prefs,
    Future<void> Function()? onUnauthorized,
  })  : _api = apiService,
        _stt = sttService,
        _tts = ttsService,
        _prefs = prefs,
        _onUnauthorized = onUnauthorized {
    _ttsEnabled = prefs.getBool('pai_tts_enabled') ?? true;
    _cache = MessageCache(prefs);
    // Paint the last known conversation before the network is even touched.
    _messages.addAll(_cache.load());
    _configureVoiceServices();
  }

  final ApiService _api;
  final SttService _stt;
  final TtsService _tts;
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

  // ── Getters ───────────────────────────────────────────────────────────────

  List<ChatMessage> get messages => List.unmodifiable(_messages);
  bool get isThinking => _isThinking;
  VoiceState get voiceState => _voiceState;
  bool get isRecording => _voiceState == VoiceState.recording;
  bool get isTranscribing => _voiceState == VoiceState.transcribing;
  String? get transcribedText => _transcribedText;
  bool get ttsEnabled => _ttsEnabled;
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
              if (_ttsEnabled && result.success) _tts.speak(result.message);
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
    if (_ttsEnabled && !isError) {
      _tts.speak(text);
    }
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
  Future<void> toggleRecording() async {
    if (_voiceState == VoiceState.recording) {
      await _stopAndTranscribe();
    } else if (_voiceState == VoiceState.idle) {
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

  Future<void> _stopAndTranscribe() async {
    _voiceState = VoiceState.transcribing;
    notifyListeners();

    final text = await _stt.stopAndTranscribe();

    _voiceState = VoiceState.idle;
    if (text != null && text.trim().isNotEmpty) {
      _transcribedText = text.trim();
    }
    notifyListeners();
  }

  void cancelRecording() {
    _stt.cancelRecording();
    _voiceState = VoiceState.idle;
    notifyListeners();
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
