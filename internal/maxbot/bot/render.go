package bot

import (
	"context"
	"fmt"
	"strings"

	"telegram-emulator/internal/maxbot/models"
	"telegram-emulator/internal/maxbot/survey"

	maxbot "github.com/max-messenger/max-bot-api-client-go"
	"github.com/max-messenger/max-bot-api-client-go/schemes"
	"go.uber.org/zap"
)

// ask показывает заявителю текущий вопрос анкеты
func (b *Bot) ask(ctx context.Context, userID int64, app *models.Application) {
	question := b.engine.Question(app)
	if question == nil {
		return
	}

	text := b.questionText(app, question)
	b.send(ctx, userID, text, b.questionKeyboard(question))
}

// questionText собирает текст вопроса: прогресс, сводка, вопрос и пояснение
func (b *Bot) questionText(app *models.Application, q *survey.Question) string {
	var sb strings.Builder

	if q.State == survey.StateConfirm {
		sb.WriteString(survey.Summary(app, b.engine.Params()))
		sb.WriteString("\n")
	} else if q.State != survey.StateStop1 {
		if current, total := b.engine.Progress(app); current > 0 && total > 0 {
			sb.WriteString(fmt.Sprintf("Шаг %d из %d\n\n", current, total))
		}
	}

	sb.WriteString(q.Text)

	if q.Kind == survey.KindMulti && len(q.Selected) > 0 {
		labels := make([]string, 0, len(q.Selected))
		for _, s := range q.Selected {
			labels = append(labels, survey.Label(survey.SphereOptions, s))
		}
		sb.WriteString("\n\nОтмечено: " + strings.Join(labels, ", "))
	}

	if q.Hint != "" {
		sb.WriteString("\n\n" + q.Hint)
	}

	return sb.String()
}

// questionKeyboard формирует клавиатуру под текущий вопрос
func (b *Bot) questionKeyboard(q *survey.Question) *maxbot.Keyboard {
	rows := make([][]schemes.ButtonInterface, 0, len(q.Options)+2)

	for _, option := range q.Options {
		label := option.Label
		if q.Kind == survey.KindMulti && contains(q.Selected, option.Value) {
			label = "✅ " + label
		}

		intent := schemes.DEFAULT
		switch {
		case option.Alarm:
			intent = schemes.NEGATIVE
		case q.State == survey.StateConfirm && option.Value == "submit":
			intent = schemes.POSITIVE
		}

		rows = append(rows, maxbot.Row(maxbot.Btn(label, answerPayload(q.State, option.Value), intent)))
	}

	if q.Kind == survey.KindMulti {
		rows = append(rows, maxbot.Row(maxbot.Btn("Ничего из этого", answerPayload(q.State, survey.SphereNone))))
		rows = append(rows, maxbot.Row(maxbot.Btn("Готово", answerPayload(q.State, "done"), schemes.POSITIVE)))
	}

	if q.Contact {
		rows = append(rows, maxbot.Row(maxbot.BtnContact("Отправить мой номер")))
	}

	if q.Skip {
		rows = append(rows, maxbot.Row(maxbot.Btn("Пропустить", answerPayload(q.State, "skip"))))
	}

	if len(rows) == 0 {
		return nil
	}

	return maxbot.InlineKeyboard(rows...)
}

// menuKeyboard главное меню бота
func (b *Bot) menuKeyboard() *maxbot.Keyboard {
	rows := [][]schemes.ButtonInterface{
		maxbot.Row(maxbot.Btn("Оставить обращение", "m:apply", schemes.POSITIVE)),
		maxbot.Row(maxbot.Btn("Как это работает", "m:about")),
		maxbot.Row(maxbot.Btn("Если человеку плохо прямо сейчас", "m:emergency", schemes.NEGATIVE)),
		maxbot.Row(maxbot.Btn("Мои обращения", "m:my")),
	}

	if b.cfg.Organization.PublicURL != "" {
		rows = append(rows, maxbot.Row(maxbot.BtnLink("Наш паблик", b.cfg.Organization.PublicURL)))
	}

	return maxbot.InlineKeyboard(rows...)
}

// send отправляет сообщение заявителю
func (b *Bot) send(ctx context.Context, userID int64, text string, keyboard *maxbot.Keyboard) {
	if strings.TrimSpace(text) == "" {
		return
	}

	if err := b.outbox.Send(ctx, Message{UserID: userID, Text: text, Keyboard: keyboard}); err != nil {
		b.log.Error("не удалось отправить сообщение", zap.Int64("user_id", userID), zap.Error(err))
	}
}

// answerCallback подтверждает нажатие кнопки
func (b *Bot) answerCallback(ctx context.Context, callbackID, notification string) {
	if callbackID == "" {
		return
	}

	if err := b.outbox.Answer(ctx, callbackID, notification); err != nil {
		b.log.Debug("не удалось подтвердить нажатие кнопки", zap.Error(err))
	}
}

// answerPayload собирает payload кнопки ответа
func answerPayload(state, value string) string {
	return "q:" + state + ":" + value
}

// parseAnswerPayload разбирает payload кнопки ответа
func parseAnswerPayload(payload string) (string, string, bool) {
	if !strings.HasPrefix(payload, "q:") {
		return "", "", false
	}

	parts := strings.SplitN(strings.TrimPrefix(payload, "q:"), ":", 2)
	if len(parts) != 2 {
		return "", "", false
	}

	return parts[0], parts[1], true
}

// contactPhone извлекает номер телефона из присланного контакта
func contactPhone(attachments []interface{}) string {
	for _, attachment := range attachments {
		contact, ok := attachment.(*schemes.ContactAttachment)
		if !ok {
			continue
		}
		if phone := phoneFromVCF(contact.Payload.VcfInfo); phone != "" {
			return phone
		}
	}

	return ""
}

// phoneFromVCF достаёт номер телефона из карточки в формате VCF
func phoneFromVCF(vcf string) string {
	for _, line := range strings.Split(vcf, "\n") {
		line = strings.TrimSpace(line)
		upper := strings.ToUpper(line)
		if !strings.HasPrefix(upper, "TEL") {
			continue
		}
		idx := strings.LastIndex(line, ":")
		if idx < 0 || idx+1 >= len(line) {
			continue
		}
		if phone, ok := survey.NormalizePhone(line[idx+1:]); ok {
			return phone
		}
	}

	return ""
}

func contains(values []string, value string) bool {
	for _, v := range values {
		if v == value {
			return true
		}
	}

	return false
}
