import 'package:flutter/foundation.dart';

enum MessageRole { user, assistant }

@immutable
class ChatMessage {
  const ChatMessage({
    required this.id,
    required this.role,
    required this.text,
    required this.timestamp,
    this.isError = false,
  });

  final String id;
  final MessageRole role;
  final String text;
  final DateTime timestamp;
  final bool isError;

  ChatMessage copyWith({String? text, bool? isError}) => ChatMessage(
        id: id,
        role: role,
        text: text ?? this.text,
        timestamp: timestamp,
        isError: isError ?? this.isError,
      );

  factory ChatMessage.fromApi(Map<String, dynamic> json) {
    final roleStr = (json['role'] as String? ?? 'assistant').toLowerCase();
    return ChatMessage(
      id: json['id']?.toString() ?? _genId(),
      role: roleStr == 'user' ? MessageRole.user : MessageRole.assistant,
      text: json['content']?.toString() ?? json['text']?.toString() ?? '',
      timestamp:
          DateTime.tryParse(json['timestamp']?.toString() ?? '') ??
          DateTime.now(),
    );
  }

  static String _genId() =>
      DateTime.now().microsecondsSinceEpoch.toString();

  static String generateId() => _genId();
}
