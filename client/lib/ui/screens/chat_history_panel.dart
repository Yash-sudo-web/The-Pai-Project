import 'package:flutter/material.dart';
import 'package:flutter_animate/flutter_animate.dart';
import 'package:provider/provider.dart';

import '../../app.dart';
import '../../models/chat_message.dart';
import '../../providers/chat_provider.dart';

/// Slide-out Chat History panel that overlays the main content.
class ChatHistoryPanel extends StatefulWidget {
  const ChatHistoryPanel({super.key});

  @override
  State<ChatHistoryPanel> createState() => _ChatHistoryPanelState();
}

class _ChatHistoryPanelState extends State<ChatHistoryPanel> {
  List<Map<String, dynamic>> _sessions = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _fetchSessions();
  }

  Future<void> _fetchSessions() async {
    final provider = context.read<ChatProvider>();
    final sessions = await provider.fetchSessions();
    if (mounted) {
      setState(() {
        _sessions = sessions;
        _loading = false;
      });
    }
  }

  void _openSession(Map<String, dynamic> session) {
    final sessionId = session['id'] as String?;
    if (sessionId == null) return;

    Navigator.of(context).pop(); // close history panel

    // Show session messages in a new overlay
    showDialog(
      context: context,
      barrierColor: Colors.black54,
      builder: (_) => _SessionViewer(
        sessionId: sessionId,
        sessionDate: session['session_date'] as String? ?? '',
        status: session['status'] as String? ?? 'closed',
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.black54,
      child: Row(
        children: [
          // Panel
          Container(
            width: 320,
            decoration: const BoxDecoration(
              color: kSurface,
              border: Border(right: BorderSide(color: kBorder, width: 1)),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // Header
                Padding(
                  padding: const EdgeInsets.fromLTRB(20, 20, 12, 16),
                  child: Row(
                    children: [
                      const Text(
                        'Chat History',
                        style: TextStyle(
                          color: kAccent,
                          fontSize: 18,
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                      const Spacer(),
                      IconButton(
                        icon: const Icon(Icons.close_rounded,
                            color: kTextSecondary, size: 20),
                        onPressed: () => Navigator.of(context).pop(),
                      ),
                    ],
                  ),
                ),

                const Divider(height: 1, color: kBorder),

                // Sessions list
                Expanded(
                  child: _loading
                      ? const Center(
                          child: CircularProgressIndicator(
                              color: kAccent, strokeWidth: 2))
                      : _sessions.isEmpty
                          ? const Center(
                              child: Text(
                                'No sessions yet.\nStart a conversation!',
                                textAlign: TextAlign.center,
                                style: TextStyle(
                                    color: kTextMuted,
                                    fontSize: 13,
                                    height: 1.6),
                              ),
                            )
                          : ListView.separated(
                              padding: const EdgeInsets.all(16),
                              itemCount: _sessions.length,
                              separatorBuilder: (_, __) =>
                                  const Divider(color: kBorder, height: 24),
                              itemBuilder: (context, index) {
                                return _SessionCard(
                                  session: _sessions[index],
                                  onTap: () =>
                                      _openSession(_sessions[index]),
                                );
                              },
                            ),
                ),
              ],
            ),
          ).animate().slideX(begin: -1, duration: 250.ms, curve: Curves.easeOut),

          // Tap outside to close
          Expanded(
            child: GestureDetector(
              onTap: () => Navigator.of(context).pop(),
              behavior: HitTestBehavior.opaque,
              child: const SizedBox.expand(),
            ),
          ),
        ],
      ),
    );
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// SESSION CARD
// ═══════════════════════════════════════════════════════════════════════════════

class _SessionCard extends StatelessWidget {
  const _SessionCard({required this.session, this.onTap});
  final Map<String, dynamic> session;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final date = session['session_date'] as String? ?? '';
    final messageCount = session['message_count'] as int? ?? 0;
    final summary = session['summary'] as String?;
    final status = session['status'] as String? ?? 'closed';
    final isActive = status == 'active';

    final dateLabel = _formatDateLabel(date);

    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(10),
      child: Container(
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: kSurfaceVar,
          borderRadius: BorderRadius.circular(10),
          border: Border.all(
            color: isActive ? kAccent.withOpacity(0.3) : kBorder,
          ),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Date + Status
            Row(
              children: [
                Text(
                  dateLabel,
                  style: const TextStyle(
                    color: kTextPrimary,
                    fontSize: 14,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                const SizedBox(width: 8),
                _StatusBadge(isActive: isActive),
                const Spacer(),
                const Icon(Icons.chevron_right_rounded,
                    color: kTextMuted, size: 18),
              ],
            ),
            const SizedBox(height: 6),

            // Message count
            Row(
              children: [
                const Icon(Icons.chat_bubble_outline_rounded,
                    color: kTextMuted, size: 13),
                const SizedBox(width: 6),
                Text(
                  '$messageCount messages',
                  style: const TextStyle(color: kTextMuted, fontSize: 12),
                ),
              ],
            ),

            // Summary
            if (summary != null && summary.isNotEmpty) ...[
              const SizedBox(height: 10),
              Container(
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: kBg,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Text(
                  summary,
                  style: const TextStyle(
                    color: kTextSecondary,
                    fontSize: 12,
                    height: 1.5,
                  ),
                  maxLines: 8,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ] else ...[
              const SizedBox(height: 6),
              Text(
                isActive ? 'Session in progress...' : 'No summary available',
                style: const TextStyle(
                  color: kTextMuted,
                  fontSize: 12,
                  fontStyle: FontStyle.italic,
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }

  String _formatDateLabel(String dateStr) {
    try {
      final date = DateTime.parse(dateStr);
      final now = DateTime.now();
      final today = DateTime(now.year, now.month, now.day);
      final yesterday = today.subtract(const Duration(days: 1));
      final sessionDay = DateTime(date.year, date.month, date.day);

      if (sessionDay == today) return 'Today';
      if (sessionDay == yesterday) return 'Yesterday';

      const weekdays = [
        'Monday', 'Tuesday', 'Wednesday', 'Thursday',
        'Friday', 'Saturday', 'Sunday'
      ];
      const months = [
        'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
        'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'
      ];

      return '${weekdays[date.weekday - 1]} ${date.day} ${months[date.month - 1]}';
    } catch (_) {
      return dateStr;
    }
  }
}

class _StatusBadge extends StatelessWidget {
  const _StatusBadge({required this.isActive});
  final bool isActive;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: isActive
            ? kSuccess.withOpacity(0.15)
            : kTextMuted.withOpacity(0.15),
        borderRadius: BorderRadius.circular(4),
        border: Border.all(
          color: isActive
              ? kSuccess.withOpacity(0.4)
              : kTextMuted.withOpacity(0.3),
        ),
      ),
      child: Text(
        isActive ? 'ACTIVE' : 'CLOSED',
        style: TextStyle(
          color: isActive ? kSuccess : kTextMuted,
          fontSize: 9,
          fontWeight: FontWeight.w700,
          letterSpacing: 0.5,
        ),
      ),
    );
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// SESSION VIEWER — full-screen overlay showing a past session's messages
// ═══════════════════════════════════════════════════════════════════════════════

class _SessionViewer extends StatefulWidget {
  const _SessionViewer({
    required this.sessionId,
    required this.sessionDate,
    required this.status,
  });
  final String sessionId;
  final String sessionDate;
  final String status;

  @override
  State<_SessionViewer> createState() => _SessionViewerState();
}

class _SessionViewerState extends State<_SessionViewer> {
  List<ChatMessage>? _messages;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _loadMessages();
  }

  Future<void> _loadMessages() async {
    final provider = context.read<ChatProvider>();
    final msgs = await provider.fetchSessionMessages(widget.sessionId);
    if (mounted) {
      setState(() {
        _messages = msgs;
        _loading = false;
      });
    }
  }

  String _formatDate(String dateStr) {
    try {
      final date = DateTime.parse(dateStr);
      final now = DateTime.now();
      final today = DateTime(now.year, now.month, now.day);
      if (DateTime(date.year, date.month, date.day) == today) return 'Today';

      const months = [
        'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
        'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'
      ];
      return '${date.day} ${months[date.month - 1]} ${date.year}';
    } catch (_) {
      return dateStr;
    }
  }

  @override
  Widget build(BuildContext context) {
    final isActive = widget.status == 'active';

    return Dialog(
      backgroundColor: kSurface,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(16),
        side: const BorderSide(color: kBorder),
      ),
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 600, maxHeight: 700),
        child: Column(
          children: [
            // Header
            Padding(
              padding: const EdgeInsets.fromLTRB(24, 20, 12, 16),
              child: Row(
                children: [
                  const Icon(Icons.history_rounded, color: kAccent, size: 20),
                  const SizedBox(width: 10),
                  Text(
                    'Session — ${_formatDate(widget.sessionDate)}',
                    style: const TextStyle(
                      color: kTextPrimary,
                      fontSize: 16,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                  const SizedBox(width: 10),
                  _StatusBadge(isActive: isActive),
                  const Spacer(),
                  IconButton(
                    icon: const Icon(Icons.close_rounded,
                        color: kTextSecondary, size: 20),
                    onPressed: () => Navigator.of(context).pop(),
                  ),
                ],
              ),
            ),
            const Divider(height: 1, color: kBorder),

            // Messages
            Expanded(
              child: _loading
                  ? const Center(
                      child: CircularProgressIndicator(
                          color: kAccent, strokeWidth: 2))
                  : (_messages == null || _messages!.isEmpty)
                      ? const Center(
                          child: Text(
                            'No messages in this session.',
                            style:
                                TextStyle(color: kTextMuted, fontSize: 13),
                          ),
                        )
                      : ListView.builder(
                          padding: const EdgeInsets.all(20),
                          itemCount: _messages!.length,
                          itemBuilder: (context, index) {
                            final msg = _messages![index];
                            final isUser =
                                msg.role == MessageRole.user;
                            return Padding(
                              padding: const EdgeInsets.only(bottom: 12),
                              child: Row(
                                mainAxisAlignment: isUser
                                    ? MainAxisAlignment.end
                                    : MainAxisAlignment.start,
                                crossAxisAlignment:
                                    CrossAxisAlignment.start,
                                children: [
                                  if (!isUser) ...[
                                    CircleAvatar(
                                      radius: 14,
                                      backgroundColor:
                                          kPrimary.withOpacity(0.2),
                                      child: const Icon(
                                          Icons.auto_awesome,
                                          size: 14,
                                          color: kPrimaryLight),
                                    ),
                                    const SizedBox(width: 8),
                                  ],
                                  Flexible(
                                    child: Container(
                                      padding: const EdgeInsets.symmetric(
                                          horizontal: 14, vertical: 10),
                                      decoration: BoxDecoration(
                                        color: isUser
                                            ? kPrimary.withOpacity(0.2)
                                            : kSurfaceVar,
                                        borderRadius:
                                            BorderRadius.circular(12),
                                        border: Border.all(
                                          color: isUser
                                              ? kPrimary.withOpacity(0.3)
                                              : kBorder,
                                        ),
                                      ),
                                      child: Column(
                                        crossAxisAlignment:
                                            CrossAxisAlignment.start,
                                        children: [
                                          Text(
                                            msg.text,
                                            style: const TextStyle(
                                              color: kTextPrimary,
                                              fontSize: 13,
                                              height: 1.5,
                                            ),
                                          ),
                                          const SizedBox(height: 4),
                                          Text(
                                            _formatTime(msg.timestamp),
                                            style: const TextStyle(
                                              color: kTextMuted,
                                              fontSize: 10,
                                            ),
                                          ),
                                        ],
                                      ),
                                    ),
                                  ),
                                  if (isUser) ...[
                                    const SizedBox(width: 8),
                                    CircleAvatar(
                                      radius: 14,
                                      backgroundColor:
                                          kAccent.withOpacity(0.15),
                                      child: const Icon(
                                          Icons.person_rounded,
                                          size: 14,
                                          color: kAccent),
                                    ),
                                  ],
                                ],
                              ),
                            );
                          },
                        ),
            ),

            // Footer
            Container(
              padding: const EdgeInsets.all(16),
              decoration: const BoxDecoration(
                border: Border(top: BorderSide(color: kBorder)),
              ),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Text(
                    '${_messages?.length ?? 0} messages',
                    style:
                        const TextStyle(color: kTextMuted, fontSize: 12),
                  ),
                  const SizedBox(width: 16),
                  if (!isActive)
                    TextButton.icon(
                      onPressed: () {
                        // Load into main chat
                        context
                            .read<ChatProvider>()
                            .loadSessionById(widget.sessionId);
                        Navigator.of(context).pop();
                      },
                      icon: const Icon(Icons.open_in_new_rounded,
                          size: 16),
                      label: const Text('Load in Chat'),
                      style: TextButton.styleFrom(
                          foregroundColor: kAccent),
                    ),
                ],
              ),
            ),
          ],
        ),
      ),
    ).animate().fadeIn(duration: 200.ms).scaleXY(begin: 0.95, duration: 200.ms);
  }

  String _formatTime(DateTime? dt) {
    if (dt == null) return '';
    return '${dt.hour.toString().padLeft(2, '0')}:${dt.minute.toString().padLeft(2, '0')}';
  }
}
