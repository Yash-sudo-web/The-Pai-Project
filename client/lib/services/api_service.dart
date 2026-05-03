import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import '../config.dart';
import '../models/chat_message.dart';

class ApiException implements Exception {
  const ApiException(this.message, {this.statusCode});
  final String message;
  final int? statusCode;
  @override
  String toString() => message;
}

class CommandResult {
  const CommandResult({
    required this.success,
    required this.message,
    this.needsConfirmation = false,
    this.pendingId,
  });
  final bool success;
  final String message;
  final bool needsConfirmation;
  final String? pendingId;
}

class ApiService {
  ApiService({required SharedPreferences prefs}) : _prefs = prefs;

  final SharedPreferences _prefs;
  final _client = http.Client();

  Map<String, String> get _headers => {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ${AppConfig.getApiKey(_prefs)}',
      };

  String get _baseUrl => AppConfig.getBaseUrl(_prefs);

  // ── Send command ─────────────────────────────────────────────────────────

  Future<CommandResult> sendCommand(String command) async {
    final uri = Uri.parse('$_baseUrl/command');
    try {
      final response = await _client
          .post(uri, headers: _headers, body: jsonEncode({'command': command}))
          .timeout(const Duration(seconds: 60));

      if (response.statusCode == 401) {
        throw const ApiException(
          'Authentication failed — check your API key in Settings.',
          statusCode: 401,
        );
      }
      if (response.statusCode != 200) {
        throw ApiException(
          'Server error (${response.statusCode})',
          statusCode: response.statusCode,
        );
      }

      final data = jsonDecode(response.body) as Map<String, dynamic>;
      final status = data['status'] as String? ?? 'completed';

      if (status == 'pending_confirmation') {
        return CommandResult(
          success: false,
          message: 'This action needs your confirmation.',
          needsConfirmation: true,
          pendingId: data['pending_id'] as String?,
        );
      }

      final result = (data['result'] as Map<String, dynamic>?) ?? {};
      return CommandResult(
        success: result['success'] as bool? ?? true,
        message: result['message'] as String? ?? 'Done.',
      );
    } on ApiException {
      rethrow;
    } catch (e) {
      throw ApiException('Network error: $e');
    }
  }

  // ── History ───────────────────────────────────────────────────────────────

  Future<List<ChatMessage>> loadHistory({int limit = 60}) async {
    try {
      final uri = Uri.parse('$_baseUrl/chat/history?limit=$limit');
      final response =
          await _client.get(uri, headers: _headers).timeout(const Duration(seconds: 15));
      if (response.statusCode != 200) return [];
      final data = jsonDecode(response.body) as Map<String, dynamic>;
      final raw = data['messages'] as List<dynamic>? ?? [];
      return raw.map((m) => ChatMessage.fromApi(m as Map<String, dynamic>)).toList();
    } catch (_) {
      return [];
    }
  }

  /// Fetch a list of past sessions with summaries.
  Future<List<Map<String, dynamic>>> fetchSessions({int limit = 30}) async {
    try {
      final uri = Uri.parse('$_baseUrl/chat/sessions?limit=$limit');
      final response =
          await _client.get(uri, headers: _headers).timeout(const Duration(seconds: 15));
      if (response.statusCode != 200) return [];
      final data = jsonDecode(response.body) as Map<String, dynamic>;
      final raw = data['sessions'] as List<dynamic>? ?? [];
      return raw.cast<Map<String, dynamic>>();
    } catch (_) {
      return [];
    }
  }

  /// Fetch messages for a specific session by ID.
  Future<List<ChatMessage>> fetchSessionMessages(String sessionId, {int limit = 100}) async {
    try {
      final uri = Uri.parse('$_baseUrl/chat/session/$sessionId/messages?limit=$limit');
      final response =
          await _client.get(uri, headers: _headers).timeout(const Duration(seconds: 15));
      if (response.statusCode != 200) return [];
      final data = jsonDecode(response.body) as Map<String, dynamic>;
      final raw = data['messages'] as List<dynamic>? ?? [];
      return raw.map((m) => ChatMessage.fromApi(m as Map<String, dynamic>)).toList();
    } catch (_) {
      return [];
    }
  }

  // ── Confirmation ──────────────────────────────────────────────────────────

  Future<bool> confirmAction(String pendingId) async {
    try {
      final r = await _client
          .post(Uri.parse('$_baseUrl/confirm/$pendingId'), headers: _headers)
          .timeout(const Duration(seconds: 15));
      return r.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  Future<bool> rejectAction(String pendingId) async {
    try {
      final r = await _client
          .post(Uri.parse('$_baseUrl/reject/$pendingId'), headers: _headers)
          .timeout(const Duration(seconds: 15));
      return r.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  // ── Health ────────────────────────────────────────────────────────────────

  Future<Map<String, dynamic>?> checkHealth() async {
    try {
      final r = await _client
          .get(Uri.parse('$_baseUrl/health'), headers: _headers)
          .timeout(const Duration(seconds: 10));
      if (r.statusCode == 200) return jsonDecode(r.body) as Map<String, dynamic>;
      return null;
    } catch (_) {
      return null;
    }
  }
}
