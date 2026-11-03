(function () {
  // Server-owned bridge for the website: shows the answer in words. Nothing is recorded in v0,
  // and it talks to nothing outside this page.
  var payload = {};
  try {
    payload = JSON.parse(document.getElementById("tutor-params").textContent) || {};
  } catch (error) {
    payload = {};
  }
  function words(answer) {
    if (!answer || typeof answer !== "object") { return ""; }
    var kind = answer.answer_kind;
    if (kind === "choice_index") {
      var options = payload.drawn && payload.drawn.options;
      var index = Number(answer.option_index);
      if (options && options[index] !== undefined) { return String(options[index]); }
      return "opción " + (index + 1);
    }
    if (kind === "values" || kind === "sequence") {
      return (answer.values || []).map(String).join(", ");
    }
    if (kind === "self_report") { return String(answer.rating) + " de 4"; }
    return String(answer.value === undefined ? "" : answer.value);
  }
  function show(text) {
    var box = document.getElementById("tutor-answer");
    if (!box) {
      box = document.createElement("p");
      box.id = "tutor-answer";
      box.setAttribute("role", "status");
      box.style.fontWeight = "600";
      document.body.appendChild(box);
    }
    box.textContent = text;
  }
  window.tutorSubmit = function (answer) {
    show("Tu respuesta: " + words(answer) + ".");
  };
})();
