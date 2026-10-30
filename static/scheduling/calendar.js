/* Shared booking calendar. All business data and permitted links come from Django. */
(() => {
  "use strict";
  const root = document.getElementById("booking-calendar");
  if (!root) return;
  const config = JSON.parse(document.getElementById("calendar-config").textContent);
  const params = new URLSearchParams(location.search);
  const error = document.getElementById("calendar-error");
  const count = document.getElementById("calendar-count");
  const hideEmpty = document.getElementById("calendar-hide-empty");
  const dialog = document.getElementById("calendar-detail");
  const detailBody = document.getElementById("calendar-detail-body");
  const detailBadges = document.getElementById("calendar-detail-badges");
  const printButton = document.getElementById("calendar-detail-print");
  const selected = new Set(config.offerings.map(item => item.id));
  let range = ["day", "week", "month"].includes(params.get("range")) ? params.get("range") : "week";
  const requestedDate = params.get("date") || "";
  const parsedDate = /^\d{4}-\d{2}-\d{2}$/.test(requestedDate) ? new Date(`${requestedDate}T00:00:00Z`) : new Date(NaN);
  let anchorDate = Number.isFinite(parsedDate.getTime()) && parsedDate.toISOString().slice(0,10) === requestedDate
    && requestedDate >= "1900-01-01" && requestedDate <= "2200-12-31" ? requestedDate : config.today;
  let pendingNavigation = false;
  let visibleStart = anchorDate;
  let requestNumber = 0, detailNumber = 0, controller;
  const node = (tag, text, className) => {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (className) el.className = className;
    return el;
  };
  const viewName = () => range === "month" ? "dayGridMonth" : range === "week" ? "timeGridWeek" : config.location ? "timeGridDay" : "resourceTimelineDay";
  const money = (amount, currency) => new Intl.NumberFormat(undefined, {style:"currency", currency}).format(amount / 100);
  function detailLink(path) {
    const current = new URL(location.href), url = new URL(path, location.origin);
    url.searchParams.set("calendar", current.pathname + current.search);
    return url.pathname + url.search;
  }
  function updateSummary(summary={unpaid:0,balance:0,empty:0}) {
    document.getElementById("calendar-unpaid").textContent=summary.unpaid;
    document.getElementById("calendar-balances").textContent=summary.balance;
    document.getElementById("calendar-empty").textContent=summary.empty;
  }
  function syncUrl() {
    const url = new URL(location.href);
    url.searchParams.set("range", range);
    url.searchParams.set("date", anchorDate);
    history.replaceState(null, "", url);
    const form = document.getElementById("dashboard-location-filter");
    if (form) for (const key of ["view", "range", "date"]) {
      let input = form.querySelector(`input[name="${key}"]`);
      if (!input) { input = node("input"); input.type = "hidden"; input.name = key; form.append(input); }
      input.value = key === "view" ? "calendar" : url.searchParams.get(key);
    }
  }
  function openDay(date) {range="day"; anchorDate=typeof date === "string" ? date.slice(0,10) : date.toISOString().slice(0,10); setRange();}
  function displayEvents(events) {
    if (config.location || range !== "week") return events;
    const groups = new Map();
    for (const event of events) {
      const key = `${event.start}/${event.end}`;
      if (!groups.has(key)) groups.set(key,[]);
      groups.get(key).push(event);
    }
    return [...groups.values()].flatMap(group => {
      if (group.length < 3) return group;
      const locations = [...new Set(group.map(event => event.extendedProps.location))];
      return [{...group[0],id:`group-${group[0].id}`,title:`${group.length} sessions`,
        extendedProps:{kind:"group",size:group.length,location:locations.length === 1 ? locations[0] : `${locations.length} locations`,published:true,
          seats:group.reduce((sum,event)=>sum+event.extendedProps.seats,0),
          capacity:group.reduce((sum,event)=>sum+event.extendedProps.capacity,0)}}];
    });
  }
  async function openDetail(event) {
    if (range === "month" || event.extendedProps.kind === "group") {openDay(event.start); return;}
    const current = ++detailNumber;
    printButton.disabled = true;
    detailBadges.replaceChildren();
    document.getElementById("calendar-detail-title").textContent = event.title;
    detailBody.replaceChildren(node("p", "Loading session..."));
    if (!dialog.open) dialog.showModal();
    try {
      const response = await fetch(event.extendedProps.detailUrl, {headers:{Accept:"application/json"}});
      if (!response.ok) throw new Error();
      const data = await response.json();
      if (current !== detailNumber || !dialog.open) return;
      document.getElementById("calendar-detail-title").textContent = data.title;
      detailBadges.replaceChildren(node("span",data.category,"calendar-detail-badge"));
      detailBody.replaceChildren(node("p", data.time), node("p", [data.location,data.room].filter(Boolean).join(" · "), "muted"));
      const summary = data.summary;
      const metrics = node("div",undefined,"calendar-detail-metrics");
      function metric(label,value,caption) {
        const item=node("div",undefined,"calendar-detail-metric");
        item.append(node("span",label),node("strong",value),node("small",caption));metrics.append(item);return item;
      }
      const booked=metric("Booked",`${summary.booked} of ${summary.capacity}`,"seats filled");
      const progress=node("progress"); progress.max=Math.max(1,summary.capacity);progress.value=summary.booked;
      progress.setAttribute("aria-label","Booked seats");booked.append(progress);
      metric("Open",summary.open,"seats left");
      metric("Collected",summary.payments.length ? summary.payments.map(p=>money(p.received,p.order__currency)).join(" + ") : "—",
        summary.payments.length ? `of ${summary.payments.map(p=>money(p.expected,p.order__currency)).join(" + ")} expected` : "No payments");
      metric("Needs action",summary.needs_action,summary.needs_action ? "payment review" : "No payment issues");
      detailBody.append(metrics,node("h3",`${summary.reservation_count} reservation${summary.reservation_count === 1 ? "" : "s"}`));
      if (data.reservations.length) {
        const wrap = node("div", undefined, "calendar-roster"), table = node("table"), head = node("tr");
        for (const label of ["Customer", "Seats", "Payment", ""]) head.append(node("th", label));
        const thead = node("thead"), body = node("tbody"); thead.append(head); table.append(thead,body);
        for (const row of data.reservations) {
          const tr = node("tr");
          const customer=node("td"), identity=node("div",undefined,"calendar-customer");
          identity.append(node("span",row.name));customer.append(identity);tr.append(customer,node("td",`${row.quantity} ${row.quantity === 1 ? "seat" : "seats"}`));
          const payment=node("td"), status=node("span",row.payment_status,`calendar-payment-status ${row.payment_status === "Paid" ? "paid" : "pending"}`);
          payment.append(status,node("small",`${money(row.received,row.currency)} received · ${money(Math.max(0,row.total-row.received),row.currency)} balance`,"calendar-payment-amount"));tr.append(payment);
          const cell = node("td"), link = node("a","View","dash-action-btn dash-action-btn--secondary"); link.href = detailLink(row.url);link.setAttribute("aria-label",`View reservation for ${row.name}`); cell.append(link); tr.append(cell);
          ["Customer", "Participants", "Payment", ""].forEach((label, index) => tr.children[index].setAttribute("data-label", label));
          body.append(tr);
        }
        wrap.append(table); detailBody.append(wrap);
      } else detailBody.append(node("p","No active reservations."));
      if (data.truncated) detailBody.append(node("p","Showing the first 500 reservations."));
      printButton.disabled = false;
    } catch (_) {
      if (current === detailNumber && dialog.open) {
        const retry = node("button", "Retry"); retry.type="button"; retry.addEventListener("click", () => openDetail(event));
        detailBody.replaceChildren(node("p","Could not load this session. Please try again."),retry);
      }
    }
  }
  document.getElementById("calendar-detail-close").addEventListener("click", () => dialog.close());
  printButton.addEventListener("click", () => {if (dialog.open && !printButton.disabled) window.print();});
  document.getElementById("calendar-prep-print").addEventListener("click", () => {
    const url = new URL(config.printUrl, location.origin);
    url.searchParams.set("print", "1");
    url.searchParams.set("period", range);
    url.searchParams.set("date", visibleStart);
    if (config.location) url.searchParams.set("location", config.location);
    if (!selected.size) url.searchParams.append("offering", "none");
    else if (selected.size !== config.offerings.length) for (const id of selected) url.searchParams.append("offering", id);
    window.open(url, "_blank", "noopener");
  });
  dialog.addEventListener("close", () => {detailNumber++;});
  const calendar = EventCalendar.create(root, {
    view:viewName(), date:anchorDate,
    timeZone:"UTC", headerToolbar:false, height:"680px", allDaySlot:false, nowIndicator:false, dayMaxEvents:true,
    editable:false, selectable:false, eventStartEditable:false, eventDurationEditable:false,
    scrollTime:"09:00:00", slotMinTime:"09:00:00", slotMaxTime:"19:00:00", flexibleSlotTimeLimits:true,
    slotDuration:"01:00:00", slotHeight:48, slotLabelInterval:"01:00:00",
    slotWidth:Math.max(88,Math.floor((root.clientWidth-180)/10)), resources:config.resources,
    datesSet(info) {
      visibleStart=info.view.currentStart.toISOString().slice(0,10);
      if (pendingNavigation) {anchorDate=info.view.currentStart.toISOString().slice(0,10);pendingNavigation=false;}
      document.getElementById("calendar-range").textContent = info.view.title;
      queueMicrotask(syncUrl);
    },
    eventSources:[{events:async (info, success) => {
      const current = ++requestNumber;
      if (controller) controller.abort(); controller = new AbortController();
      error.hidden = true; count.textContent = "Loading..."; updateSummary({unpaid:"...",balance:"...",empty:"..."});
      if (!selected.size) {success([]); updateSummary(); count.textContent="No workshops selected"; return;}
      const url = new URL(config.eventsUrl, location.origin);
      url.searchParams.set("start",info.startStr.slice(0,10)); url.searchParams.set("end",info.endStr.slice(0,10));
      url.searchParams.set("location",config.location);
      if (selected.size !== config.offerings.length) for (const id of selected) url.searchParams.append("offering",id);
      if (hideEmpty.checked) url.searchParams.set("hide_empty","1");
      try {
        const response = await fetch(url,{signal:controller.signal,headers:{Accept:"application/json"}});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Could not load the calendar.");
        if (current !== requestNumber) return;
        success(displayEvents(data.events)); count.textContent = `${data.events.length} events`;
        updateSummary(data.summary);
      } catch (failure) {
        if (current !== requestNumber || failure.name === "AbortError") return;
        success([]); updateSummary({unpaid:"?",balance:"?",empty:"?"}); count.textContent=""; error.hidden=false;
        error.querySelector("span").textContent = failure.message === "Failed to fetch" ? "Could not load the calendar. Check your connection." : "Could not load the calendar. Please try again or select a shorter date range.";
      }
    }}],
    eventClassNames({event}) {
      const p=event.extendedProps;
      return [p.kind==="party" ? "event-party" : p.camp ? "event-camp" : p.seats===0 ? "event-empty" : p.seats>=p.capacity/2 ? "event-full" : "event-partial"];
    },
    eventContent({event,timeText}) {
      const p=event.extendedProps;
      if (range === "week") {
        const size=p.kind === "group" ? p.size : 1;
        return {domNodes:[
          node("strong",`${size} session${size === 1 ? "" : "s"}`,"calendar-event-title"),
          node("span",p.location,"calendar-event-location"),
          node("span",timeText,"calendar-event-meta"),
          node("span",`${p.seats}/${p.capacity} participants${p.published ? "" : " · Draft"}`,"calendar-event-meta")
        ]};
      }
      const title=node("strong",config.location || range === "day" ? event.title : p.location,"calendar-event-title");
      const detail=node("span",`${timeText} · ${p.seats}/${p.capacity} participants${p.published ? "" : " · Draft"}`,"calendar-event-meta");
      return {domNodes:[title,detail]};
    },
    eventClick:({event}) => openDetail(event),
    eventDidMount({el,event}) {
      el.tabIndex=0; el.setAttribute("role","button"); el.setAttribute("aria-label",`${event.title}, ${event.extendedProps.location}, ${event.extendedProps.seats} of ${event.extendedProps.capacity} participants`);
      el.addEventListener("keydown",e=>{if(e.key==="Enter" || e.key===" "){e.preventDefault();openDetail(event);}});
    },
    dateClick(info) {openDay(info.date);},
    dayCellContent({date}) {
      if (range !== "month") return;
      const button=node("button",String(date.getUTCDate()),"calendar-day-heading");button.type="button";
      button.setAttribute("aria-label",`Open day ${date.toISOString().slice(0,10)}`);
      button.addEventListener("click",event=>{event.stopPropagation();openDay(date);});return {domNodes:[button]};
    },
    dayHeaderFormat(date) {
      if (range === "month") return new Intl.DateTimeFormat(undefined,{weekday:"short",timeZone:"UTC"}).format(date);
      const label = new Intl.DateTimeFormat(undefined,{weekday:"short",day:"numeric",timeZone:"UTC"}).format(date);
      const button=node("button",label,"calendar-day-heading"); button.type="button";
      button.setAttribute("aria-label",`Open day ${date.toISOString().slice(0,10)}`);
      button.addEventListener("click",()=>openDay(date));return {domNodes:[button]};
    },
    resourceLabelContent({resource}) {
      const item=config.resources.find(item=>item.id===resource.id);
      const button=node("button",resource.title,"calendar-location-heading");button.type="button";
      button.addEventListener("click",()=>{
        const url=new URL(location.href);url.searchParams.set("location",resource.id);url.searchParams.set("view","calendar");location.assign(url);
      });
      const zone=node("small",item ? item.zone.replaceAll("_"," ") : "");
      return {domNodes:[button,zone]};
    },
  });
  // The bundled calendar exposes overflow content, but no more-link click hook.
  // Capture its native trigger before the library opens the month popover.
  function openOverflow(event) {
    if (event.type === "keydown" && event.key !== "Enter" && event.key !== " ") return;
    const target=range === "month" ? event.target : event.target.closest('.ec-day-foot a[role="button"]');
    const date=target?.closest('.ec-day')?.querySelector('time[datetime]')?.getAttribute('datetime');
    if (!date) return;
    event.preventDefault();event.stopPropagation();openDay(date);
  }
  root.addEventListener("click",openOverflow,true);
  root.addEventListener("keydown",openOverflow,true);
  function setRange() {
    root.dataset.range=range;
    calendar.setOption("view",viewName());
    calendar.setOption("date",anchorDate);
    calendar.setOption("height",range === "day" && !config.location ? "480px" : "680px");
    calendar.refetchEvents();
    document.querySelectorAll("[data-calendar-range]").forEach(button=>button.setAttribute("aria-pressed",String(button.dataset.calendarRange===range)));
    syncUrl();
  }
  document.querySelectorAll("[data-calendar-range]").forEach(button=>button.addEventListener("click",()=>{range=button.dataset.calendarRange;setRange();}));
  document.querySelectorAll("[data-calendar-action]").forEach(button=>button.addEventListener("click",()=>{
    const action=button.dataset.calendarAction;
    if(action==="today") {anchorDate=config.today;calendar.setOption("date",anchorDate);syncUrl();}
    else {pendingNavigation=true;calendar[action]();}
  }));
  for (const item of config.offerings) {
    const label=node("label",undefined,"calendar-check"), input=node("input"); input.type="checkbox"; input.checked=true; input.value=item.id;
    input.addEventListener("change",()=>{if(input.checked)selected.add(item.id);else selected.delete(item.id);calendar.refetchEvents();});
    label.append(input,node("span",item.title));document.getElementById("calendar-offerings").append(label);
  }
  document.querySelectorAll("[data-calendar-filter]").forEach(button=>button.addEventListener("click",()=>{
    selected.clear(); const all=button.dataset.calendarFilter==="all";
    document.querySelectorAll("#calendar-offerings input").forEach(input=>{input.checked=all;if(all)selected.add(input.value);});calendar.refetchEvents();
  }));
  hideEmpty.addEventListener("change",()=>calendar.refetchEvents());
  document.getElementById("calendar-retry").addEventListener("click",()=>calendar.refetchEvents());
  setRange();
})();
