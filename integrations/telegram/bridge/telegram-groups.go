package provisionutil

import (
	"context"
	"encoding/json"
	"strconv"
	"strings"

	"maunium.net/go/mautrix"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/networkid"
)

func IsTelegramGroupID(peer string) bool {
	kind, value, _ := strings.Cut(peer, ":")
	number, err := strconv.ParseInt(value, 10, 64)
	return (kind == "chat" || kind == "channel") && err == nil && number > 0 && strconv.FormatInt(number, 10) == value
}

func ResolveTelegramGroup(ctx context.Context, login *bridgev2.UserLogin, peer string, open bool) (*bridgev2.Portal, error) {
	if !IsTelegramGroupID(peer) {
		return nil, mautrix.MNotFound.WithMessage("Use an existing Telegram group ID")
	}
	if login.Client == nil || !login.Client.IsLoggedIn() {
		return nil, bridgev2.ErrNotLoggedIn
	}
	portal, err := login.Bridge.GetExistingPortalByKey(ctx, networkid.PortalKey{ID: networkid.PortalID(peer), Receiver: login.ID})
	if err != nil {
		return nil, mautrix.MUnknown.WithMessage("Could not read the Telegram group").WithInternalError(err)
	}
	if portal == nil {
		return nil, mautrix.MNotFound.WithMessage("Group not found. Run !tg sync-chats in Telegram Bridge and retry")
	}
	if portal.ParentKey.ID != "" || portal.UserBlocked || portal.MessageRequest {
		return nil, mautrix.MForbidden.WithMessage("This Telegram group cannot be opened")
	}
	if strings.HasPrefix(peer, "channel:") {
		var flags struct {
			Supergroup   bool `json:"is_supergroup"`
			ForumGeneral bool `json:"is_forum_general"`
		}
		metadata, err := json.Marshal(portal.Metadata)
		if err != nil || json.Unmarshal(metadata, &flags) != nil || !flags.Supergroup || flags.ForumGeneral {
			return nil, mautrix.MForbidden.WithMessage("Only Telegram groups without topics can be opened")
		}
	}
	if open && portal.MXID == "" {
		info, err := login.Client.GetChatInfo(ctx, portal)
		if err != nil {
			return nil, mautrix.MUnknown.WithMessage("Could not verify the Telegram group").WithInternalError(err)
		}
		if info == nil {
			return nil, mautrix.MNotFound.WithMessage("Telegram group information is unavailable")
		}
		if err = portal.CreateMatrixRoom(ctx, login, info); err != nil {
			return nil, mautrix.MUnknown.WithMessage("Could not open the Telegram group").WithInternalError(err)
		}
	}
	return portal, nil
}
