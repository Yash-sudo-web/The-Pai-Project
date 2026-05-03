import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../config.dart';
import '../models/chat_message.dart';
import '../services/api_service.dart';
import '../services/stt_service.dart';
import '../services/tts_service.dart';

enum VoiceState { idle, recording, transcribing }

class ChatProvider extends ChangeNotifier {
  ChatProvider({
    required ApiService apiService,
    required SttService sttService,
    required TtsService ttsService,
    required SharedPreferences prefs,
  })  : _api = apiService,
        _stt = sttService,
        _tts = ttsService,
        _prefs = prefs {
    _ttsEnabled = prefs.getBool('pai_tts_enabled') ?? true;
    _isConfigured = AppConfig.isConfigured(prefs);
    _configureVoiceServices();
  }

  final ApiService _api;
  final SttService _stt;
  final TtsService _tts;
  final SharedPreferences _prefs;

  final List<ChatMessage> _messages = [];
  bool _isThinking = false;
  VoiceState _voiceState = VoiceState.idle;
  String? _transcribedText; // text to put into the input box
  late bool _ttsEnabled;
  late bool _isConfigured;
  String? _pendingConfirmationId;
  String? _statusError;
  bool _isOnline = true;

  // ── Getters ───────────────────────────────────────────────────────────────

  List<ChatMessage> get messages => List.unmodifiable(_messages);
  bool get isThinking => _isThinking;
  VoiceState get voiceState => _voiceState;
  bool get isRecording => _voiceState == VoiceState.recording;
  bool get isTranscribing => _voiceState == VoiceState.transcribing;
  String? get transcribedText => _transcribedText;
  bool get ttsEnabled => _ttsEnabled;
  bool get isConfigured => _isConfigured;
  String? get statusError => _statusError;
  bool get hasPendingConfirmation => _pendingConfirmationId != null;
  bool get isOnline => _isOnline;

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
    _isConfigured = AppConfig.isConfigured(_prefs);
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
    notifyListeners();

    try {
      final result = await _api.sendCommand(trimmed);
      _isOnline = true;

      if (result.needsConfirmation && result.pendingId != null) {
        _pendingConfirmationId = result.pendingId;
        _addAssistant(
          '⚠️ This action requires your confirmation — use the buttons below.',
        );
      } else {
        _addAssistant(result.message, isError: !result.success);
      }
    } on ApiException catch (e) {
      if (e.statusCode == 401) {
        _statusError = 'Auth failed — check Settings.';
      }
      _addAssistant('Error: $e', isError: true);
      _isOnline = false;
    } catch (e) {
      _addAssistant('Network error: $e', isError: true);
      _isOnline = false;
    } finally {
      _isThinking = false;
      notifyListeners();
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
      _startRecording();
    }
  }

  void _startRecording() {
    final ok = _stt.startRecording();
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
      return messages.length;
    } catch (_) {
      return 0;
    }
  }

  Future<int> loadHistory() async {
    try {
      final history = await _api.loadHistory();
      if (history.isEmpty) return 0;
      _messages.clear();
      _messages.addAll(history);
      notifyListeners();
      return history.length;
    } catch (_) {
      return 0;
    }
  }

  void clearChat() {
    _messages.clear();
    _tts.stop();
    _pendingConfirmationId = null;
    notifyListeners();
  }
}
