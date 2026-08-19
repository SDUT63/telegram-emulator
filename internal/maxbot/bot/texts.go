package bot

import (
	"fmt"
	"strings"

	"telegram-emulator/internal/maxbot/config"
	"telegram-emulator/internal/maxbot/models"
)

// greeting приветствие и объяснение навигационной функции службы (фаза 7.1)
func greeting(cfg *config.Config) string {
	var b strings.Builder

	b.WriteString(fmt.Sprintf("Здравствуйте! Это служба навигации «%s», %s.\n\n",
		cfg.Organization.ServiceName, cfg.Organization.Name))
	b.WriteString("Мы не оказываем помощь сами. Мы помогаем разобраться, куда обратиться, " +
		"передаём обращение в профильную организацию и проверяем, дошла ли помощь до человека.\n\n")
	b.WriteString("Обратиться может кто угодно: сам человек, родственник, сосед или специалист.\n\n")
	b.WriteString("Что дальше: я задам несколько вопросов — это займёт 5–7 минут. " +
		"Потом с вами свяжется координатор.")

	return b.String()
}

// aboutText объясняет, что служба делает и чего не делает
func aboutText(cfg *config.Config) string {
	var b strings.Builder

	b.WriteString("Что мы делаем:\n")
	b.WriteString("• принимаем обращение от любого человека в интересах гражданина;\n")
	b.WriteString("• помогаем понять, чья это компетенция, и выбираем направление;\n")
	b.WriteString("• передаём сведения в профильную организацию — с вашего согласия;\n")
	b.WriteString("• через неделю звоним и спрашиваем, началась ли помощь на самом деле.\n\n")

	b.WriteString("Чего мы не делаем:\n")
	b.WriteString("• не оказываем медицинскую, социальную и юридическую помощь;\n")
	b.WriteString("• не назначаем лечение и не даём рекомендаций по уходу;\n")
	b.WriteString("• не устанавливаем уровень нуждаемости — это делает эксперт;\n")
	b.WriteString("• не обещаем срок и объём чужой услуги.\n\n")

	b.WriteString("Какие данные мы собираем: имя и контакт, кем вы приходитесь человеку, " +
		"его имя, возраст и адрес, что произошло с ваших слов, с чем он не справляется сам.\n")
	b.WriteString("Диагноз, СНИЛС, полис, паспортные данные, сведения о доходах и имуществе " +
		"мы не собираем: они не влияют на выбор направления.\n")

	if cfg.Organization.PolicyURL != "" {
		b.WriteString("\nПолитика обработки персональных данных: " + cfg.Organization.PolicyURL)
	}

	return b.String()
}

// emergencyText объясняет, что делать в экстренной ситуации
func emergencyText() string {
	return "Если человеку прямо сейчас плохо — не пишите нам, а вызовите скорую помощь: " +
		"103 с мобильного или 112.\n\n" +
		"Мы — служба навигации: мы не выезжаем и не оказываем медицинскую помощь. " +
		"Когда ситуация будет под контролем, вернитесь и оставьте обращение — " +
		"мы поможем с уходом и дальнейшими шагами."
}

// stop1Text утверждённая формулировка при выявлении признака STOP-1
func stop1Text() string {
	return "Я останавливаю анкету.\n\n" +
		"По тому, что вы описали, ситуация может требовать неотложной медицинской помощи. " +
		"Пожалуйста, прямо сейчас вызовите скорую: 103 с мобильного или 112.\n\n" +
		"Я записал(а) это обращение. Координатор свяжется с вами на следующий рабочий день, " +
		"чтобы вернуться к вопросу об уходе. Если ситуация изменится — напишите нам."
}

// closingText завершение разговора: четыре обязательных элемента (ОС, р. 7.7)
func closingText(cfg *config.Config, app *models.Application) string {
	var b strings.Builder

	b.WriteString("Спасибо. Обращение записано, его номер: " + app.PublicID + "\n\n")
	b.WriteString("Что будет дальше:\n")
	b.WriteString("• я записал(а) ваше обращение и передал(а) его координатору;\n")
	b.WriteString("• координатор свяжется с вами в течение одного рабочего дня, назовёт организацию " +
		"и оформит согласие на передачу сведений именно туда;\n")
	b.WriteString("• через неделю мы позвоним и спросим, началась ли помощь — " +
		"мы отслеживаем, доходит ли человек до помощи на самом деле;\n")
	b.WriteString("• если за неделю с вами никто не свяжется — напишите нам сюда ещё раз")
	if cfg.Organization.Phone != "" {
		b.WriteString(" или позвоните: " + cfg.Organization.Phone)
	}
	b.WriteString(".\n\n")
	b.WriteString("Мы не называем срок и объём чужой услуги, пока принимающая сторона его не подтвердила. " +
		"Срок нашего действия — один рабочий день.")

	return b.String()
}

// lineClosedText сообщение, когда перечень STOP-1 не утверждён
func lineClosedText(cfg *config.Config) string {
	var b strings.Builder

	b.WriteString("Приём обращений через чат-бот пока не открыт.\n\n")
	b.WriteString("Перечень признаков экстренного состояния утверждает медицинский специалист. " +
		"Пока перечень не подписан, мы не принимаем обращения: это правило безопасности.\n\n")
	if cfg.Organization.Phone != "" {
		b.WriteString("Позвоните нам: " + cfg.Organization.Phone)
		if cfg.Organization.WorkHours != "" {
			b.WriteString(" (" + cfg.Organization.WorkHours + ")")
		}
		b.WriteString(".\n")
	}
	b.WriteString("Если человеку плохо прямо сейчас — вызовите скорую: 103 или 112.")

	return b.String()
}

// declinedText сообщение при отказе от согласия на обработку данных
func declinedText(cfg *config.Config) string {
	text := "Понимаю. Без согласия на обработку данных мы не можем принять обращение и передать его " +
		"в организацию — можем только рассказать в общем виде, как устроена помощь.\n\n" +
		"Если передумаете, напишите /start — вернёмся к анкете."
	if cfg.Organization.Phone != "" {
		text += "\nТелефон службы: " + cfg.Organization.Phone
	}

	return text
}

// helpText краткая справка по командам
func helpText(cfg *config.Config) string {
	var b strings.Builder

	b.WriteString("Команды бота:\n")
	b.WriteString("/start — начать сначала и открыть меню;\n")
	b.WriteString("/apply — оставить обращение;\n")
	b.WriteString("/my — мои обращения;\n")
	b.WriteString("/cancel — прервать заполнение анкеты;\n")
	b.WriteString("/help — эта справка.\n\n")
	b.WriteString("Если человеку плохо прямо сейчас — вызовите скорую: 103 или 112.")
	if cfg.Organization.Phone != "" {
		b.WriteString("\nТелефон службы: " + cfg.Organization.Phone)
		if cfg.Organization.WorkHours != "" {
			b.WriteString(" (" + cfg.Organization.WorkHours + ")")
		}
	}

	return b.String()
}
