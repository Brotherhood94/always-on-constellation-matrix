package commands

import (
	"strings"
	"testing"

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
