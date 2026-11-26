package connector

import (
	"context"
	"errors"
	"strconv"
	"strings"

	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tgerr"
	"maunium.net/go/mautrix/bridgev2/networkid"
)

func (tc *TelegramClient) ConstellationSyncGroups(ctx context.Context) error {
	if tc.metadata.IsBot {
		return errors.New("a Telegram user login is required")
	}
	return tc.syncChats(ctx, 0, false, true)
}

func (tc *TelegramClient) ConstellationInvite(ctx context.Context, portal networkid.PortalID, members []string) ([]map[string]string, error) {
	peer, topic, err := tc.inputPeerForPortalID(ctx, portal)
	if err != nil || topic != 0 || tc.metadata.IsBot {
		return nil, errors.New("a logged-in user's Telegram group is required")
	}
	results := make([]map[string]string, 0, len(members))
	for _, member := range members {
		userID, err := strconv.ParseInt(strings.TrimPrefix(member, "user:"), 10, 64)
		if err != nil || userID <= 0 {
			return nil, errors.New("invalid Telegram participant")
		}
		user, err := tc.getInputUser(ctx, userID)
		var invited *tg.MessagesInvitedUsers
		status := "not_added"
		if err == nil {
			switch target := peer.(type) {
			case *tg.InputPeerChat:
				invited, err = tc.client.API().MessagesAddChatUser(ctx, &tg.MessagesAddChatUserRequest{
					ChatID: target.ChatID, UserID: user, FwdLimit: 0,
				})
			case *tg.InputPeerChannel:
				invited, err = tc.client.API().ChannelsInviteToChannel(ctx, &tg.ChannelsInviteToChannelRequest{
					Channel: &tg.InputChannel{ChannelID: target.ChannelID, AccessHash: target.AccessHash},
					Users:   []tg.InputUserClass{user},
				})
			default:
				return nil, errors.New("unsupported Telegram group")
			}
			if err != nil {
				if rpc, ok := tgerr.As(err); ok {
					if rpc.IsOneOf("USER_ALREADY_PARTICIPANT") {
						status = "already_member"
					}
				} else {
					status = "unconfirmed"
				}
			} else if invited != nil && len(invited.MissingInvitees) == 0 {
				status = "added"
			}
		}
		results = append(results, map[string]string{"peer": member, "status": status})
	}
	return results, nil
}

func (tc *TelegramClient) ConstellationHistory(ctx context.Context, portal networkid.PortalID, since, before int64) ([]map[string]any, bool, error) {
	peer, topic, err := tc.inputPeerForPortalID(ctx, portal)
	if err != nil || topic != 0 || tc.metadata.IsBot {
		return nil, false, errors.New("a logged-in user's Telegram chat is required")
	}
	const limit = 50
	history, err := tc.client.API().MessagesGetHistory(ctx, &tg.MessagesGetHistoryRequest{
		Peer: peer, Limit: limit, OffsetDate: int((before + 999) / 1000),
	})
	if err != nil {
		return nil, false, err
	}
	messages, ok := history.(tg.ModifiedMessagesMessages)
	if !ok {
		return nil, false, errors.New("unsupported Telegram history")
	}
	rows := messages.GetMessages()
	result := make([]map[string]any, 0, len(rows))
	complete := len(rows) < limit
	for _, row := range rows {
		msg, ok := row.(*tg.Message)
		if !ok {
			continue
		}
		stamp := int64(msg.Date) * 1000
		if stamp < since {
			complete = true
			break
		}
		if stamp >= before || strings.TrimSpace(msg.Message) == "" {
			continue
		}
		author := "participant"
		if msg.Out {
			author = "operator"
		} else if sender, ok := msg.FromID.(*tg.PeerUser); ok {
			author = "Telegram user " + strconv.FormatInt(sender.UserID, 10)
		}
		body := []rune(msg.Message)
		if len(body) > 1000 {
			body = body[:1000]
			complete = false
		}
		result = append(result, map[string]any{"author": author, "ts": stamp, "body": string(body)})
	}
	return result, complete, nil
}
