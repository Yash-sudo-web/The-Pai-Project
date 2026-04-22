import 'package:flutter/material.dart';
import 'package:flutter_animate/flutter_animate.dart';

import '../../app.dart';

class ThinkingIndicator extends StatelessWidget {
  const ThinkingIndicator({super.key});

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.start,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // PAI avatar
        Container(
          width: 32,
          height: 32,
          margin: const EdgeInsets.only(top: 4),
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            gradient: const LinearGradient(
              colors: [kPrimary, Color(0xFF4C1D95)],
              begin: Alignment.topLeft,
              end: Alignment.bottomRight,
            ),
          ),
          child: const Icon(Icons.auto_awesome_rounded,
              color: Colors.white, size: 15),
        )
            .animate(onPlay: (c) => c.repeat(reverse: true))
            .scaleXY(end: 1.08, duration: 700.ms, curve: Curves.easeInOut),
        const SizedBox(width: 10),
        // Dot container
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
          decoration: BoxDecoration(
            color: kSurface,
            borderRadius: const BorderRadius.only(
              topLeft: Radius.circular(4),
              topRight: Radius.circular(16),
              bottomLeft: Radius.circular(16),
              bottomRight: Radius.circular(16),
            ),
            border: Border.all(color: kBorder),
          ),
          child: const _ThreeDots(),
        ),
      ],
    );
  }
}

class _ThreeDots extends StatelessWidget {
  const _ThreeDots();

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: List.generate(3, (i) {
        return Container(
          width: 7,
          height: 7,
          margin: const EdgeInsets.symmetric(horizontal: 3),
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            color: kPrimaryLight,
          ),
        )
            .animate(delay: Duration(milliseconds: i * 160), onPlay: (c) => c.repeat())
            .moveY(begin: 0, end: -6, duration: 480.ms, curve: Curves.easeInOut)
            .then()
            .moveY(begin: -6, end: 0, duration: 480.ms, curve: Curves.easeInOut);
      }),
    );
  }
}
