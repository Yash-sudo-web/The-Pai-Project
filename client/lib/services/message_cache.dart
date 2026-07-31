import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

import '../models/chat_message.dart';

/// Local cache of the current conversation.
///
/// The app used to hold messages in memory only and refetch on every launch,
/// so a cold start showed an empty screen until the network answered. This
/// paints the last known conversation immediately and keeps it readable
/// offline; the server response replaces it as soon as it arrives.
///
/// Backed by SharedPreferences rather than SQLite: the payload is a bounded
/// list of short strings, and this works identically on mobile, desktop, and
/// web with no platform-specific setup.
class MessageCache {
  MessageCache(this._prefs);

  final SharedPreferences _prefs;

  static const _key = 'pai_cached_messages';
  static const _keyStamp = 'pai_cached_messages_at';

  /// Keeping the newest slice bounds both storage and parse time on launch.
  static const maxMessages = 100;

  List<ChatMessage> load() {
    final raw = _prefs.getString(_key);
    if (raw == null || raw.isEmpty) return const [];

    try {
      final decoded = jsonDecode(raw) as List<dynamic>;
      return decoded
          .map((item) => _fromJson(item as Map<String, dynamic>))
          .whereType<ChatMessage>()
          .toList();
    } catch (_) {
      // A corrupt cache is never worth surfacing — drop it and move on.
      unawaited(clear());
      return const [];
    }
  }

  DateTime? get cachedAt {
    final raw = _prefs.getString(_keyStamp);
    return raw == null ? null : DateTime.tryParse(raw);
  }

  Future<void> save(List<ChatMessage> messages) async {
    if (messages.isEmpty) {
      await clear();
      return;
    }

    final recent = messages.length > maxMessages
        ? messages.sublist(messages.length - maxMessages)
        : messages;

    await _prefs.setString(_key, jsonEncode(recent.map(_toJson).toList()));
    await _prefs.setString(_keyStamp, DateTime.now().toIso8601String());
  }

  Future<void> clear() async {
    await _prefs.remove(_key);
    await _prefs.remove(_keyStamp);
  }

  static Map<String, dynamic> _toJson(ChatMessage m) => {
        'id': m.id,
        'role': m.role == MessageRole.user ? 'user' : 'assistant',
        'text': m.text,
        'timestamp': m.timestamp.toIso8601String(),
        'isError': m.isError,
      };

  static ChatMessage? _fromJson(Map<String, dynamic> json) {
    final text = json['text'] as String?;
    if (text == null) return null;
    return ChatMessage(
      id: json['id'] as String? ?? ChatMessage.generateId(),
      role: json['role'] == 'user' ? MessageRole.user : MessageRole.assistant,
      text: text,
      timestamp: DateTime.tryParse(json['timestamp'] as String? ?? '') ?? DateTime.now(),
      isError: json['isError'] as bool? ?? false,
    );
  }
}

/// Local no-op so a fire-and-forget clear() does not trip the lint.
void unawaited(Future<void> future) {
  future.catchError((_) {});
}
