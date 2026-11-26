package connector

import (
	"reflect"
	"testing"

	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

func TestMatchingSavedContacts(t *testing.T) {
	users := []tg.UserClass{
		&tg.User{ID: 1001, FirstName: "Sample", LastName: "Person", Username: "sample_handle"},
		&tg.User{ID: 1002, FirstName: "Sample", LastName: "Person", Deleted: true},
		&tg.User{ID: 1003, FirstName: "Sample", LastName: "Contact"},
		&tg.UserEmpty{ID: 1004},
	}
	for _, test := range []struct {
		query string
		want  []int64
	}{
		{"  sAmPlE   PeRsOn ", []int64{1001}},
		{"sample", []int64{1001, 1003}},
		{"@sample", []int64{1001}},
		{"@person", nil},
		{"", nil},
	} {
		var got []int64
		for _, match := range matchingSavedContacts(users, test.query) {
			got = append(got, match.GetID())
		}
		if !reflect.DeepEqual(got, test.want) {
			t.Errorf("search %q returned %v, want %v", test.query, got, test.want)
		}
	}
	var many []tg.UserClass
	for i := range userSearchLimit + 1 {
		many = append(many, &tg.User{ID: int64(i + 1), FirstName: "Sample"})
	}
	if got := len(matchingSavedContacts(many, "Sample")); got != userSearchLimit {
		t.Errorf("search returned %d users, want %d", got, userSearchLimit)
	}
}
