import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../config.dart';
import '../models/chat_message.dart';
import '../services/api_service.dart';
import '../services/stt_service.dart';
import '../services/tts_service.dart';

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
  bool _isRecording = false;
  String _liveTranscript = '';
  late bool _ttsEnabled;
  late bool _isConfigured;
  String? _pendingConfirmationId;
  String? _statusError;
  bool _isOnline = true;

  // ── Getters ───────────────────────────────────────────────────────────────

  List<ChatMessage> get messages => List.unmodifiable(_messages);
  bool get isThinking => _isThinking;
  bool get isRecording => _isRecording;
  String get liveTranscript => _liveTranscript;
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

  Future<void> startRecording() async {
    if (_isRecording || _isThinking) return;
    _isRecording = true;
    _liveTranscript = 'Listening…';
    notifyListeners();

    await _stt.startListening(
      onResult: (text, isFinal) {
        // Groq STT doesn't stream — this callback isn't used in the new flow
      },
    );
  }

  Future<void> stopRecordingAndSend() async {
    if (!_isRecording) return;
    _liveTranscript = 'Transcribing…';
    notifyListeners();

    final transcript = await _stt.stopListeningAndTranscribe();
    _isRecording = false;
    _liveTranscript = '';
    notifyListeners();

    if (transcript != null && transcript.trim().isNotEmpty) {
      await sendCommand(transcript);
    }
  }

  Future<void> cancelRecording() async {
    await _stt.cancel();
    _isRecording = false;
    _liveTranscript = '';
    notifyListeners();
  }

  // ── History ───────────────────────────────────────────────────────────────

  Future<void> loadHistory() async {
    final history = await _api.loadHistory();
    if (history.isEmpty) return;
    _messages.clear();
    _messages.addAll(history);
    notifyListeners();
  }

  void clearChat() {
    _messages.clear();
    _tts.stop();
    _pendingConfirmationId = null;
    notifyListeners();
  }
}
