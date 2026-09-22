import Foundation

/// A single turn in the on-screen transcript. This is purely local/UI
/// state -- the real conversation memory lives on the Windows PC server
/// (jarvis/brain/memory.py), so the iOS app doesn't need to persist much
/// itself; re-launching just starts a fresh-looking transcript view while
/// the server still remembers prior context.
struct ChatMessage: Identifiable, Equatable {
    enum Sender: Equatable {
        case user
        case jarvis
        case system // local status/error messages, not sent to the server
    }

    let id = UUID()
    let sender: Sender
    let text: String
    let timestamp = Date()
}
