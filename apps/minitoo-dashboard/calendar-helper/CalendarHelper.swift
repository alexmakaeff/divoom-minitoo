import EventKit
import Foundation

// calendar-helper: writes today's timed (non-all-day) events and open reminders
// due up to today as JSON for the MiniToo dashboard. Built as a .app bundle so macOS attributes Calendar access
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

func hasAccess(_ type: EKEntityType) -> Bool {
    let status = EKEventStore.authorizationStatus(for: type)
    if #available(macOS 14.0, *) { return status == .fullAccess }
    return status == .authorized
}

func requestAccess(_ store: EKEventStore, _ type: EKEntityType) -> Bool {
    let sema = DispatchSemaphore(value: 0)
    var granted = false
    if #available(macOS 14.0, *) {
        if type == .event {
            store.requestFullAccessToEvents { ok, _ in granted = ok; sema.signal() }
        } else {
            store.requestFullAccessToReminders { ok, _ in granted = ok; sema.signal() }
        }
    } else {
        store.requestAccess(to: type) { ok, _ in granted = ok; sema.signal() }
    }
    _ = sema.wait(timeout: .now() + 120)
    return granted
}

func statusName(_ type: EKEntityType, allowed: Bool) -> String {
    if allowed { return "ok" }
    return EKEventStore.authorizationStatus(for: type) == .notDetermined ? "not_determined" : "denied"
}

guard let outPath = argValue("--out") else {
    FileHandle.standardError.write(Data("usage: calendar-helper --out <path> [--request-access]\n".utf8))
    exit(64)
}

let wantsAccess = CommandLine.arguments.contains("--request-access")
var eventsAllowed = hasAccess(.event)
var remindersAllowed = hasAccess(.reminder)
if wantsAccess && !eventsAllowed { eventsAllowed = requestAccess(EKEventStore(), .event) }
if wantsAccess && !remindersAllowed { remindersAllowed = requestAccess(EKEventStore(), .reminder) }

// A fresh store sees calendars granted during this run.
let store = EKEventStore()
let cal = Calendar.current
let start = cal.startOfDay(for: Date())
let end = cal.date(byAdding: .day, value: 1, to: start)!
var result: [String: Any] = ["status": statusName(.event, allowed: eventsAllowed),
                             "reminders_status": statusName(.reminder, allowed: remindersAllowed)]

if eventsAllowed {
    let predicate = store.predicateForEvents(withStart: start, end: end, calendars: nil)
    result["events"] = store.events(matching: predicate).filter { !$0.isAllDay }.map { ev -> [String: Any] in
        ["title": ev.title ?? "",
         "start": ev.startDate.timeIntervalSince1970,
         "end": ev.endDate.timeIntervalSince1970,
         "calendar": ev.calendar?.title ?? ""]
    }
}

if remindersAllowed {
    // Incomplete reminders due up to the end of today, overdue ones included.
    // A reminder with a date but no time is "all_day"; its due is local midnight.
    let predicate = store.predicateForIncompleteReminders(withDueDateStarting: nil, ending: end, calendars: nil)
    let sema = DispatchSemaphore(value: 0)
    var items: [[String: Any]] = []
    store.fetchReminders(matching: predicate) { reminders in
        for reminder in reminders ?? [] {
            guard let comps = reminder.dueDateComponents, let due = cal.date(from: comps), due < end else { continue }
            items.append(["title": reminder.title ?? "",
                          "due": due.timeIntervalSince1970,
                          "all_day": comps.hour == nil,
                          "calendar": reminder.calendar?.title ?? ""])
        }
        sema.signal()
    }
    _ = sema.wait(timeout: .now() + 20)
    result["reminders"] = items
}

write(result, to: outPath)
