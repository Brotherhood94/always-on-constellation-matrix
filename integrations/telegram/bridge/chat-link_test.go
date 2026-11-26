package commands

import (
	"strings"
	"testing"

	"maunium.net/go/mautrix/bridgev2/provisionutil"
	"maunium.net/go/mautrix/id"
)

func TestOpenedChatLink(t *testing.T) {
	room := id.RoomID("!portal:fixture.test")
	for _, test := range []struct {
		name, wantLabel, prefix string
		created                 bool
	}{
		{"Eugenio & team", "Eugenio &amp; team", "Created chat", true},
		{`<a href="https://fixture.test">name</a>`, `&lt;a href=&#34;https://fixture.test&#34;&gt;name&lt;/a&gt;`, "Open chat", false},
		{"", room.String(), "Open chat", false},
	} {
		got := formatOpenedChatLink(test.name, room, test.created)
		want := test.prefix + `: <a href="` + room.URI().MatrixToURL() + `">` + test.wantLabel + `</a>`
		if got != want || strings.Count(got, "<a ") != 1 {
			t.Errorf("opened chat reply = %q, want one room link %q", got, want)
		}
	}
}

func TestResolvedTelegramTarget(t *testing.T) {
	for _, peer := range []string{"1001", "chat:456", "channel:789"} {
		got := formatResolvedTelegramTarget("<Sample & team>", peer, "!tg", peer)
		for _, want := range []string{"&lt;Sample &amp; team&gt;", "Telegram → Rooms", "!tg resolve-identifier --open " + peer} {
			if !strings.Contains(got, want) {
				t.Errorf("lookup reply %q missing %q", got, want)
			}
		}
		if strings.Contains(got, "<a ") {
			t.Errorf("lookup reply links to a profile before the room is open: %s", got)
		}
	}
}

func TestTelegramGroupID(t *testing.T) {
	for _, peer := range []string{"chat:456", "channel:789"} {
		if !provisionutil.IsTelegramGroupID(peer) {
			t.Errorf("valid group ID rejected: %s", peer)
		}
	}
	for _, peer := range []string{"user:1001", "channel:0", "chat:+456", "channel:0456", "chat:-456", "channel:9999999999999999999"} {
		if provisionutil.IsTelegramGroupID(peer) {
			t.Errorf("invalid group ID accepted: %s", peer)
		}
	}
}
