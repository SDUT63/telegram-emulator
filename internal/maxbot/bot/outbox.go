package bot

import (
	"context"

	maxbot "github.com/max-messenger/max-bot-api-client-go"
	"github.com/max-messenger/max-bot-api-client-go/schemes"
)

// Message исходящее сообщение бота
type Message struct {
	UserID   int64
	ChatID   int64
	Text     string
	Keyboard *maxbot.Keyboard
}

// Outbox канал отправки сообщений: реализуется MAX Bot API, подменяется в тестах
type Outbox interface {
	Send(ctx context.Context, message Message) error
	Answer(ctx context.Context, callbackID, notification string) error
}

// MessagesAPI набор методов MAX Bot API, которые использует бот
type MessagesAPI interface {
	Send(ctx context.Context, m *maxbot.Message) error
	AnswerOnCallback(ctx context.Context, callbackID string, answer *schemes.CallbackAnswer) (*schemes.SimpleQueryResult, error)
}

// apiOutbox отправляет сообщения через MAX Bot API
type apiOutbox struct {
	api MessagesAPI
}

// NewOutbox создаёт канал отправки поверх MAX Bot API
func NewOutbox(api MessagesAPI) Outbox {
	return &apiOutbox{api: api}
}

// Send отправляет сообщение пользователю или в чат
func (o *apiOutbox) Send(ctx context.Context, message Message) error {
	m := maxbot.NewMessage().SetText(message.Text)
	if message.UserID != 0 {
		m = m.SetUser(message.UserID)
	}
	if message.ChatID != 0 {
		m = m.SetChat(message.ChatID)
	}
	if message.Keyboard != nil {
		m = m.AddKeyboard(message.Keyboard)
	}

	return o.api.Send(ctx, m)
}

// Answer подтверждает нажатие кнопки
func (o *apiOutbox) Answer(ctx context.Context, callbackID, notification string) error {
	_, err := o.api.AnswerOnCallback(ctx, callbackID, &schemes.CallbackAnswer{Notification: notification})

	return err
}
