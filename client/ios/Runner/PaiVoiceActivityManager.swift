import ActivityKit
import Foundation
import UIKit

@available(iOS 16.1, *)
@MainActor
final class PaiVoiceActivityManager {
  static let shared = PaiVoiceActivityManager()

  private var activity: Activity<PaiVoiceAttributes>?
  private var turnActive = false

  private init() {}

  /// Return a short status for the Flutter UI. A failed Live Activity should
  /// never be silent while the audio turn itself continues to work.
  func begin() async -> String {
    // A previous turn may have been interrupted before cleanup completed.
    await end()
    turnActive = true
    guard ActivityAuthorizationInfo().areActivitiesEnabled else {
      return "Live Activities are disabled in iPhone Settings."
    }
    do {
      let attributes = PaiVoiceAttributes(startedAt: Date())
      let state = PaiVoiceAttributes.ContentState(phase: "listening")
      if #available(iOS 16.2, *) {
        activity = try Activity.request(
          attributes: attributes,
          content: ActivityContent(
            state: state,
            staleDate: Date().addingTimeInterval(90)
          ),
          pushType: nil
        )
      } else {
        activity = try Activity.request(
          attributes: attributes,
          contentState: state,
          pushType: nil
        )
      }
      NSLog("Pai Live Activity started: %@", activity?.id ?? "unknown")
      return "Live Activity started"
    } catch {
      NSLog("Pai Live Activity could not start: %@", String(describing: error))
      return "Live Activity could not start: \(error.localizedDescription)"
    }
  }

  func update(_ phase: String) async {
    if turnActive && activity == nil && UIApplication.shared.applicationState == .active {
      _ = await begin()
    }
    guard let activity else { return }
    let state = PaiVoiceAttributes.ContentState(phase: phase)
    if #available(iOS 16.2, *) {
      let staleAfter: TimeInterval = phase == "speaking" ? 300 : 90
      await activity.update(ActivityContent(
        state: state,
        staleDate: Date().addingTimeInterval(staleAfter)
      ))
    } else {
      await activity.update(using: state)
    }
  }

  func end(showCompletion: Bool = false) async {
    turnActive = false
    self.activity = nil
    let dismissalPolicy: ActivityUIDismissalPolicy = showCompletion
      ? .after(Date().addingTimeInterval(15))
      : .immediate
    let finalState = PaiVoiceAttributes.ContentState(phase: "done")
    // Also clear an activity left by a previous process after a crash.
    for existing in Activity<PaiVoiceAttributes>.activities {
      if #available(iOS 16.2, *) {
        let content = ActivityContent(state: finalState, staleDate: nil)
        await existing.end(content, dismissalPolicy: dismissalPolicy)
      } else {
        await existing.end(using: finalState, dismissalPolicy: dismissalPolicy)
      }
    }
  }
}
