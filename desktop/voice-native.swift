import AppKit
import ApplicationServices

let arguments = CommandLine.arguments
guard arguments.count >= 2, let front = NSWorkspace.shared.frontmostApplication else { exit(2) }
if arguments[1] == "target" {
    print(front.processIdentifier)
} else if arguments[1] == "paste", arguments.count == 3,
          let target = Int32(arguments[2]), target > 0 {
    guard front.processIdentifier == target else { print("copied"); exit(0) }
    guard AXIsProcessTrusted() else { exit(3) }
    let flags = CGEventSource.flagsState(.combinedSessionState)
    guard flags.intersection([.maskCommand, .maskControl, .maskAlternate, .maskShift]).isEmpty else {
        print("copied"); exit(0)
    }
    guard let source = CGEventSource(stateID: .hidSystemState),
          let down = CGEvent(keyboardEventSource: source, virtualKey: 9, keyDown: true),
          let up = CGEvent(keyboardEventSource: source, virtualKey: 9, keyDown: false) else { exit(4) }
    down.flags = .maskCommand
    up.flags = .maskCommand
    guard NSWorkspace.shared.frontmostApplication?.processIdentifier == target else { print("copied"); exit(0) }
    down.postToPid(target)
    up.postToPid(target)
    print("sent")
} else { exit(2) }
