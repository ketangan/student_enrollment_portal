/* Same calendar renderer as bookings; List owns the data and detail destinations. */
(() => {
  "use strict";
  const root = document.getElementById("school-calendar");
  if (!root) return;
  const error = document.getElementById("school-calendar-error");
  if (typeof EventCalendar === "undefined") { error.hidden = false; return; }
  const config = JSON.parse(document.getElementById("school-calendar-config").textContent);
  const open = event => window.location.assign(event.extendedProps.url);
  const node = (tag, text, className) => {
    const item = document.createElement(tag);
    item.textContent = text;
    item.className = className;
    return item;
  };
  EventCalendar.create(root, {
    view: {day: "timeGridDay", week: "timeGridWeek", month: "dayGridMonth"}[config.range],
    date: config.date, events: config.events, firstDay: 1, timeZone: "UTC",
    headerToolbar: false, height: "680px", allDaySlot: false, nowIndicator: false,
    editable: false, selectable: false, eventStartEditable: false, eventDurationEditable: false,
    dayMaxEvents: true, scrollTime: "09:00:00", slotMinTime: "08:00:00", slotMaxTime: "19:00:00",
    flexibleSlotTimeLimits: true, slotDuration: "00:30:00", slotHeight: 44, slotLabelInterval: "01:00:00",
    eventClassNames: ({event}) => [event.extendedProps.kind === "Program session" ? "event-camp" :
      event.extendedProps.kind === "Class" ? "event-party" : "event-partial"],
    eventContent({event, timeText}) {
      const info = event.extendedProps;
      const text = [timeText, info.teacher || info.kind, info.status === "Draft" ? "Draft" : ""].filter(Boolean).join(" · ");
      return {domNodes: [node("strong", info.person || event.title, "calendar-event-title"),
        node("span", text, "calendar-event-meta")]};
    },
    eventClick: ({event}) => open(event),
    eventDidMount({el, event}) {
      const info = event.extendedProps;
      const label = [event.title, info.kind, info.person, info.teacher, info.status, info.location, info.zone].filter(Boolean).join(", ");
      el.tabIndex = 0;
      el.setAttribute("role", "link");
      el.setAttribute("aria-label", label);
      el.setAttribute("title", label);
      el.addEventListener("keydown", e => {
        if (e.key === "Enter" || e.key === " ") {e.preventDefault(); open(event);}
      });
    }
  });
})();
