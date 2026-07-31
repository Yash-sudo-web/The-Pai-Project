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

/// One frame from the `/command/stream` SSE endpoint.
enum CommandEventType { token, tool, pendingConfirmation, result, error }

class CommandEvent {
  const CommandEvent(this.type, {this.text, this.toolName, this.toolStatus, this.pendingId, this.result});

  final CommandEventType type;
  final String? text;
  final String? toolName;
  final String? toolStatus;
  final String? pendingId;
  final CommandResult? result;
}

class ApiService {
  ApiService({required SharedPreferences prefs}) : _prefs = prefs;

  final SharedPreferences _prefs;
  final _client = http.Client();

  Map<String, String> get _headers => {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ${AppConfig.getCredential(_prefs)}',
      };

  String get _baseUrl => AppConfig.getBaseUrl(_prefs);

  // ── Auth ──────────────────────────────────────────────────────────────────

  /// Whether the backend offers password login (and how long sessions last).
  Future<({bool passwordLogin, int sessionDays})> authStatus({String? baseUrl}) async {
    final root = (baseUrl ?? _baseUrl).replaceAll(RegExp(r'/+$'), '');
    final r = await _client
        .get(Uri.parse('$root/auth/status'))
        .timeout(const Duration(seconds: 10));
    if (r.statusCode != 200) {
      throw ApiException('Server error (${r.statusCode})', statusCode: r.statusCode);
    }
    final data = jsonDecode(r.body) as Map<String, dynamic>;
    return (
      passwordLogin: data['password_login'] as bool? ?? false,
      sessionDays: data['session_days'] as int? ?? 30,
    );
  }

  /// Exchange the account password for a session token.
  Future<({String token, DateTime? expiresAt})> login(
    String password, {
    String? baseUrl,
  }) async {
    final root = (baseUrl ?? _baseUrl).replaceAll(RegExp(r'/+$'), '');
    final http.Response r;
    try {
      r = await _client
          .post(
            Uri.parse('$root/auth/login'),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({'password': password}),
          )
          .timeout(const Duration(seconds: 20));
    } catch (e) {
      throw ApiException('Could not reach the server: $e');
    }

    if (r.statusCode != 200) {
      throw ApiException(_detail(r) ?? 'Login failed (${r.statusCode})',
          statusCode: r.statusCode);
    }

    final data = jsonDecode(r.body) as Map<String, dynamic>;
    return (
      token: data['access_token'] as String,
      expiresAt: DateTime.tryParse(data['expires_at'] as String? ?? ''),
    );
  }

  /// Extend the current session. Returns null when it could not be renewed.
  Future<({String token, DateTime? expiresAt})?> refreshSession() async {
    try {
      final r = await _client
          .post(Uri.parse('$_baseUrl/auth/refresh'), headers: _headers)
          .timeout(const Duration(seconds: 15));
      if (r.statusCode != 200) return null;
      final data = jsonDecode(r.body) as Map<String, dynamic>;
      return (
        token: data['access_token'] as String,
        expiresAt: DateTime.tryParse(data['expires_at'] as String? ?? ''),
      );
    } catch (_) {
      return null;
    }
  }

  /// Validate the stored credential. Returns false only on a definite 401.
  Future<bool> verifyCredential() async {
    try {
      final r = await _client
          .get(Uri.parse('$_baseUrl/auth/me'), headers: _headers)
          .timeout(const Duration(seconds: 15));
      if (r.statusCode == 401) return false;
      return true; // network/5xx: assume still valid, stay signed in offline
    } catch (_) {
      return true;
    }
  }

  static String? _detail(http.Response r) {
    try {
      final body = jsonDecode(r.body);
      if (body is Map && body['detail'] is String) return body['detail'] as String;
    } catch (_) {}
    return null;
  }

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

  /// Stream a command's reply as it is generated.
  ///
  /// Falls back to nothing on transport failure — callers should catch
  /// [ApiException] and retry with [sendCommand] if they want the blocking
  /// behaviour.
  Stream<CommandEvent> sendCommandStream(String command) async* {
    final uri = Uri.parse('$_baseUrl/command/stream');
    final request = http.Request('POST', uri)
      ..headers.addAll({..._headers, 'Accept': 'text/event-stream'})
      ..body = jsonEncode({'command': command});

    final http.StreamedResponse response;
    try {
      response = await _client.send(request).timeout(const Duration(seconds: 30));
    } catch (e) {
      throw ApiException('Network error: $e');
    }

    if (response.statusCode == 401) {
      throw const ApiException(
        'Authentication failed — check your API key in Settings.',
        statusCode: 401,
      );
    }
    if (response.statusCode != 200) {
      throw ApiException('Server error (${response.statusCode})',
          statusCode: response.statusCode);
    }

    var eventName = '';
    final dataLines = <String>[];

    final lines = response.stream.transform(utf8.decoder).transform(const LineSplitter());
    await for (final line in lines) {
      if (line.isEmpty) {
        // Blank line terminates an SSE frame.
        if (dataLines.isNotEmpty) {
          final event = _parseEvent(eventName, dataLines.join('\n'));
          if (event != null) yield event;
        }
        eventName = '';
        dataLines.clear();
        continue;
      }
      if (line.startsWith(':')) continue; // comment / keep-alive
      if (line.startsWith('event:')) {
        eventName = line.substring(6).trim();
      } else if (line.startsWith('data:')) {
        dataLines.add(line.substring(5).trim());
      }
    }
  }

  CommandEvent? _parseEvent(String name, String rawData) {
    final Map<String, dynamic> data;
    try {
      data = jsonDecode(rawData) as Map<String, dynamic>;
    } catch (_) {
      return null;
    }

    switch (name) {
      case 'token':
        return CommandEvent(CommandEventType.token, text: data['text'] as String? ?? '');
      case 'tool':
        return CommandEvent(
          CommandEventType.tool,
          toolName: data['name'] as String?,
          toolStatus: data['status'] as String?,
        );
      case 'pending_confirmation':
        return CommandEvent(
          CommandEventType.pendingConfirmation,
          pendingId: data['pending_id'] as String?,
        );
      case 'result':
        return CommandEvent(
          CommandEventType.result,
          result: CommandResult(
            success: data['success'] as bool? ?? true,
            message: data['message'] as String? ?? '',
          ),
        );
      case 'error':
        return CommandEvent(CommandEventType.error, text: data['message'] as String?);
      default:
        return null; // 'done' and anything unrecognised
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
