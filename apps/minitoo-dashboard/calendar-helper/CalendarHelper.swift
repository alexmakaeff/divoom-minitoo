import EventKit
import Foundation

// calendar-helper: writes today's timed (non-all-day) events as JSON for the
// MiniToo dashboard. Built as a .app bundle so macOS attributes Calendar access
// to this helper, not to Python or the terminal.
// Usage: calendar-helper --out <path> [--request-access]

func argValue(_ name: String) -> String? {
    let args = CommandLine.arguments
    guard let i = args.firstIndex(of: name), i + 1 < args.count else { return nil }
    return args[i + 1]
}

func write(_ object: [String: Any], to path: String) {
    let data = (try? JSONSerialization.data(withJSONObject: object)) ?? Data("{\"status\":\"error\"}".utf8)
    try? data.write(to: URL(fileURLWithPath: path), options: .atomic)
}

func hasAccess() -> Bool {
    let status = EKEventStore.authorizationStatus(for: .event)
    if #available(macOS 14.0, *) { return status == .fullAccess }
    return status == .authorized
}

func requestAccess(_ store: EKEventStore) -> Bool {
    let sema = DispatchSemaphore(value: 0)
    var granted = false
    if #available(macOS 14.0, *) {
        store.requestFullAccessToEvents { ok, _ in granted = ok; sema.signal() }
    } else {
        store.requestAccess(to: .event) { ok, _ in granted = ok; sema.signal() }
    }
    _ = sema.wait(timeout: .now() + 120)
    return granted
}

guard let outPath = argValue("--out") else {
    FileHandle.standardError.write(Data("usage: calendar-helper --out <path> [--request-access]\n".utf8))
    exit(64)
}

var allowed = hasAccess()
if !allowed && CommandLine.arguments.contains("--request-access") {
    allowed = requestAccess(EKEventStore())
}
guard allowed else {
    let status = EKEventStore.authorizationStatus(for: .event)
    write(["status": status == .notDetermined ? "not_determined" : "denied"], to: outPath)
    exit(0)
}

// A fresh store sees calendars granted during this run.
let store = EKEventStore()
let cal = Calendar.current
let start = cal.startOfDay(for: Date())
let end = cal.date(byAdding: .day, value: 1, to: start)!
let predicate = store.predicateForEvents(withStart: start, end: end, calendars: nil)
let events: [[String: Any]] = store.events(matching: predicate).filter { !$0.isAllDay }.map { ev in
    ["title": ev.title ?? "",
     "start": ev.startDate.timeIntervalSince1970,
     "end": ev.endDate.timeIntervalSince1970,
     "calendar": ev.calendar?.title ?? ""]
}
write(["status": "ok", "events": events], to: outPath)
