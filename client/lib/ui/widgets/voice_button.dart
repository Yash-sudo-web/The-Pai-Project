import 'package:flutter/material.dart';
import 'package:flutter_animate/flutter_animate.dart';

import '../../app.dart';

class VoiceButton extends StatelessWidget {
  const VoiceButton({
    super.key,
    required this.isRecording,
    required this.isDisabled,
    required this.onTap,
  });

  final bool isRecording;
  final bool isDisabled;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: isRecording ? 'Stop & send' : 'Hold to talk',
      child: GestureDetector(
        onTap: isDisabled ? null : onTap,
        child: Stack(
          alignment: Alignment.center,
          children: [
            // Pulse ring when recording
            if (isRecording)
              Container(
                width: 52,
                height: 52,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  border: Border.all(color: kError.withOpacity(0.5), width: 2),
                ),
              )
                  .animate(onPlay: (c) => c.repeat())
                  .scaleXY(begin: 1, end: 1.5, duration: 900.ms)
                  .fadeOut(duration: 900.ms),

            // Button body
            AnimatedContainer(
              duration: const Duration(milliseconds: 250),
              width: 44,
              height: 44,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                gradient: isDisabled
                    ? null
                    : LinearGradient(
                        colors: isRecording
                            ? [kError, const Color(0xFFBE123C)]
                            : [kPrimary, const Color(0xFF5B21B6)],
                        begin: Alignment.topLeft,
                        end: Alignment.bottomRight,
                      ),
                color: isDisabled ? kSurfaceVar : null,
                boxShadow: isDisabled
                    ? null
                    : [
                        BoxShadow(
                          color: (isRecording ? kError : kPrimary)
                              .withOpacity(0.35),
                          blurRadius: 14,
                          spreadRadius: 1,
                        ),
                      ],
              ),
              child: Icon(
                isRecording ? Icons.stop_rounded : Icons.mic_rounded,
                color: isDisabled ? kTextMuted : Colors.white,
                size: 20,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
