package survey

import (
	"fmt"
	"strings"

	"telegram-emulator/internal/maxbot/models"
)

// Summary формирует сводку анкеты для проверки заявителем перед отправкой
func Summary(app *models.Application, p Params) string {
	var b strings.Builder

	b.WriteString("Проверьте, пожалуйста, что записано верно:\n\n")
	b.WriteString("• Кто обращается: " + value(app.ApplicantName) + ", " + Label(RelationOptions, app.Relation) + "\n")
	b.WriteString("• Телефон: " + value(app.ApplicantPhone) + "\n")
	b.WriteString("• Кому нужна помощь: " + value(app.WardName))
	if app.WardAge > 0 {
		b.WriteString(", " + AgeLabel(app.WardAge))
	}
	b.WriteString("\n")
	if app.Relation != RelationSelf {
		b.WriteString("• Знает об обращении: " + Label(KnowsOptions, app.WardKnows) + "\n")
	}
	b.WriteString("• Район: " + value(app.District) + "\n")
	b.WriteString("• Адрес: " + value(app.Address) + "\n")
	b.WriteString("• Ситуация: " + value(app.Issue) + "\n")
	b.WriteString("• Передвижение: " + Label(AbilityOptions, app.Mobility) + "\n")
	b.WriteString("• Гигиена: " + Label(AbilityOptions, app.Hygiene) + "\n")
	b.WriteString("• Питание: " + Label(AbilityOptions, app.Food) + "\n")
	b.WriteString("• Ухаживающий: " + Label(CaregiverOptions, app.Caregiver))
	if app.CaregiverLoad != "" {
		b.WriteString(", нагрузка — " + Label(CaregiverLoadOptions, app.CaregiverLoad))
	}
	b.WriteString("\n")
	b.WriteString("• Боль или медицинский контроль на дому: " + Label(MedicalOptions, app.MedicalNeed) + "\n")
	b.WriteString("• Социальные услуги сейчас: " + Label(SocialOptions, app.SocialServices) + "\n")
	if app.PreviousRequests != "" {
		b.WriteString("• Куда уже обращались: " + app.PreviousRequests + "\n")
	}
	b.WriteString("• Другие вопросы: " + spheresLabel(app) + "\n")
	b.WriteString("• На связи через неделю: " + value(app.ContactPerson) + "\n")
	b.WriteString("• Удобное время звонка: " + Label(ContactTimeOptions, app.ContactTime) + "\n")

	return b.String()
}

// Card формирует карточку обращения для координатора
func Card(app *models.Application) string {
	var b strings.Builder

	title := "НОВОЕ ОБРАЩЕНИЕ"
	if app.Stop1Mark == Stop1Mark {
		title = "STOP-1 — ТРЕБУЕТСЯ НЕМЕДЛЕННАЯ РЕАКЦИЯ"
	}

	b.WriteString(title + " · " + app.PublicID + "\n")
	b.WriteString("Канал: " + channelLabel(app.Channel) + "\n")
	b.WriteString("Дата и время: " + app.CreatedAt.Format("02.01.2006 15:04") + "\n")
	if app.Repeat {
		b.WriteString("Пометка: повторное обращение\n")
	}
	b.WriteString("Отметка фильтра: " + value(app.Stop1Mark) + "\n")
	if app.Stop1Sign != "" {
		b.WriteString("Признак STOP-1: " + app.Stop1Sign + "\n")
		if app.Stop1At != nil {
			b.WriteString("Время выявления признака: " + app.Stop1At.Format("02.01.2006 15:04:05") + "\n")
		}
	}
	b.WriteString("Согласие на обработку данных: " + yesNo(app.ConsentPD) + "\n")
	b.WriteString("\n")

	b.WriteString("Заявитель: " + value(app.ApplicantName) + ", " + value(app.ApplicantPhone) + "\n")
	b.WriteString("Кем приходится: " + Label(RelationOptions, app.Relation) + "\n")
	if app.Username != "" {
		b.WriteString("Профиль в MAX: @" + app.Username + "\n")
	}
	b.WriteString("Подопечный: " + value(app.WardName))
	if app.WardAge > 0 {
		b.WriteString(", " + AgeLabel(app.WardAge))
	}
	b.WriteString("\n")
	if app.Relation != RelationSelf && app.WardKnows != "" {
		b.WriteString("Знает об обращении: " + Label(KnowsOptions, app.WardKnows) + "\n")
	}
	if app.District != "" || app.Address != "" {
		b.WriteString("Район и адрес: " + value(app.District) + ", " + value(app.Address) + "\n")
	}

	if app.Issue != "" {
		b.WriteString("\nСо слов заявителя:\n" + app.Issue + "\n")
	}

	if app.Stop1Mark != Stop1Mark {
		b.WriteString("\nОграничения самообслуживания:\n")
		b.WriteString("• встать и пройти — " + Label(AbilityOptions, app.Mobility) + "\n")
		b.WriteString("• помыться — " + Label(AbilityOptions, app.Hygiene) + "\n")
		b.WriteString("• приготовить и принять пищу — " + Label(AbilityOptions, app.Food) + "\n")
		b.WriteString("Ухаживающий: " + Label(CaregiverOptions, app.Caregiver))
		if app.CaregiverLoad != "" {
			b.WriteString(", нагрузка — " + Label(CaregiverLoadOptions, app.CaregiverLoad))
		}
		b.WriteString("\n")
		b.WriteString("Боль или медицинский контроль на дому: " + Label(MedicalOptions, app.MedicalNeed) + "\n")
		b.WriteString("Социальные услуги сейчас: " + Label(SocialOptions, app.SocialServices) + "\n")
		if app.PreviousRequests != "" {
			b.WriteString("Куда уже обращались: " + app.PreviousRequests + "\n")
		}
		b.WriteString("Дополнительные сферы: " + spheresLabel(app) + "\n")

		if app.RouteHint != "" {
			b.WriteString("\nПредварительный маршрут: " + app.RouteHint)
			if d, ok := RouteDescriptions[app.RouteHint]; ok {
				b.WriteString(" — " + d)
			}
			b.WriteString("\n")
			if app.RouteReason != "" {
				b.WriteString("Обоснование: " + app.RouteReason + "\n")
			}
		}
	}

	b.WriteString("\nОбратная связь: " + value(app.ContactPerson))
	if app.ContactTime != "" {
		b.WriteString(", " + Label(ContactTimeOptions, app.ContactTime))
	}
	b.WriteString("\n")

	b.WriteString("Владелец следующего действия: " + value(app.NextActionOwner))
	if app.NextActionDue != nil {
		b.WriteString(", до " + app.NextActionDue.Format("02.01.2006 15:04"))
	}
	b.WriteString("\n")

	b.WriteString("\nДиагноз, сведения о доходах и имуществе не собирались.")

	return b.String()
}

// AgeLabel склоняет слово «год» по числу лет
func AgeLabel(age int) string {
	word := "лет"
	switch {
	case age%10 == 1 && age%100 != 11:
		word = "год"
	case age%10 >= 2 && age%10 <= 4 && (age%100 < 12 || age%100 > 14):
		word = "года"
	}

	return fmt.Sprintf("%d %s", age, word)
}

func spheresLabel(app *models.Application) string {
	spheres := app.SpheresList()
	if len(spheres) == 0 {
		return "не отмечены"
	}

	labels := make([]string, 0, len(spheres))
	for _, s := range spheres {
		labels = append(labels, Label(SphereOptions, s))
	}

	return strings.Join(labels, ", ")
}

func channelLabel(channel string) string {
	if channel == "max" {
		return "чат-бот MAX"
	}
	if channel == "" {
		return "не указан"
	}

	return channel
}

func value(v string) string {
	if strings.TrimSpace(v) == "" {
		return "не указано"
	}

	return v
}

func yesNo(v bool) string {
	if v {
		return "да"
	}

	return "нет"
}
