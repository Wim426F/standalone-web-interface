// Command bridge for the remote support page (remote.html). openinverter.org's
// support script calls inverter.sendCmd() with each command a helper sends,
// and posts the reply back. This file went missing in the v4 rewrite, which
// left remote support unable to reach the inverter.
//
// With sign-in on, commands run with the rights of whoever opened the page:
// a viewer can only serve read commands. Refusals are passed back to the
// helper as text instead of failing silently.
var inverter = {
	sendCmd: function(cmd, replyFunc, repeat)
	{
		var url = "/cmd?cmd=" + encodeURIComponent(cmd);
		if (repeat) url += "&repeat=" + repeat;
		var reply = replyFunc || function() {};

		fetch(url, { credentials: "same-origin", headers: { "X-OI-Auth": "1" } })
			.then(function(r) {
				return r.text().then(function(text) {
					if (r.ok) return reply(text);
					if (r.status === 401) return reply("Error: not signed in on the device - open the web interface, sign in, then restart remote support");
					if (r.status === 403) return reply("Error: the signed-in account isn't allowed to run this command");
					reply("Error: HTTP " + r.status + (text ? " - " + text : ""));
				});
			})
			.catch(function(e) { reply("Error: " + e.message); });
	}
};
