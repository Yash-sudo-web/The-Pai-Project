import ActivityKit
import SwiftUI
import WidgetKit

@available(iOS 16.1, *)
private extension String {
  var paiTitle: String {
    switch self {
    case "listening": return "Listening"
    case "transcribing": return "Transcribing"
    case "thinking": return "Thinking"
    case "speaking": return "Speaking"
    case "interrupted": return "Interrupted"
    default: return "Pai"
    }
  }

  var paiSymbol: String {
    switch self {
    case "listening": return "mic.fill"
    case "transcribing": return "waveform"
    case "thinking": return "ellipsis.bubble.fill"
    case "speaking": return "speaker.wave.2.fill"
    case "interrupted": return "exclamationmark.circle.fill"
    default: return "circle.fill"
    }
  }
}

@available(iOS 16.1, *)
struct PaiVoiceWidget: Widget {
  var body: some WidgetConfiguration {
    ActivityConfiguration(for: PaiVoiceAttributes.self) { context in
      HStack(spacing: 12) {
        Image(systemName: context.state.phase.paiSymbol)
          .font(.title2)
          .foregroundStyle(.cyan)
        VStack(alignment: .leading, spacing: 2) {
          Text("Pai")
            .font(.headline)
          Text(context.state.phase.paiTitle)
            .font(.subheadline)
        }
        Spacer()
        Link(destination: URL(string: "pai://stop")!) {
          Text("Stop")
            .font(.subheadline.weight(.semibold))
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(.white.opacity(0.16), in: Capsule())
        }
        .accessibilityLabel("Stop Pai voice turn")
      }
      .padding()
      .activityBackgroundTint(Color(red: 0.07, green: 0.07, blue: 0.11))
      .activitySystemActionForegroundColor(.white)
    } dynamicIsland: { context in
      DynamicIsland {
        DynamicIslandExpandedRegion(.leading) {
          Label("Pai", systemImage: context.state.phase.paiSymbol)
            .foregroundStyle(.cyan)
        }
        DynamicIslandExpandedRegion(.bottom) {
          HStack {
            Text(context.state.phase.paiTitle)
              .font(.headline)
            Spacer()
            Link("Stop", destination: URL(string: "pai://stop")!)
          }
        }
      } compactLeading: {
        Image(systemName: context.state.phase.paiSymbol)
          .foregroundStyle(.cyan)
          .accessibilityLabel("Pai")
      } compactTrailing: {
        Text(context.state.phase.paiTitle)
          .font(.caption2)
          .accessibilityLabel(context.state.phase.paiTitle)
      } minimal: {
        Image(systemName: context.state.phase.paiSymbol)
          .foregroundStyle(.cyan)
          .accessibilityLabel("Pai, \(context.state.phase.paiTitle)")
      }
    }
  }
}

@main
struct PaiVoiceWidgets: WidgetBundle {
  var body: some Widget {
    PaiVoiceWidget()
  }
}
