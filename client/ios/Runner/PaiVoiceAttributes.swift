import ActivityKit
import Foundation

@available(iOS 16.1, *)
struct PaiVoiceAttributes: ActivityAttributes {
  struct ContentState: Codable, Hashable {
    var phase: String
  }

  var startedAt: Date
}
