package survey

import (
	"fmt"
	"strings"

	"telegram-emulator/internal/maxbot/models"
	"telegram-emulator/internal/maxbot/policy"
)

// RouteDescriptions расшифровка маршрутов для карточки координатора
var RouteDescriptions = map[string]string{
	"М1": "медицинский контур — отделение паллиативной медицинской помощи",
	"М2": "социальный контур — КЦСОН по району",
	"М3": "контур долговременного ухода — ТКЦ СДУ либо уполномоченный орган",
	"М4": "спорный случай — решение эксперта по нуждаемости",
	"М5": "межведомственный контур — несколько получателей",
}

// CollectFacts собирает признаки случая для матрицы маршрутов.
//
// Бот собирает факты; решение о маршруте принимает координатор,
// уровень нуждаемости устанавливает эксперт.
func CollectFacts(app *models.Application) policy.Facts {
	facts := policy.Facts{
		MedicalNeed:       app.MedicalNeed,
		Caregiver:         app.Caregiver,
		AdditionalSpheres: len(app.SpheresList()),
		CaregiverKnown:    app.Caregiver != "" && app.Caregiver != CaregiverUnknown,
		MedicalKnown:      app.MedicalNeed != "" && app.MedicalNeed != AnswerUnknown,
	}

	for _, v := range []string{app.Mobility, app.Hygiene, app.Food} {
		switch v {
		case AbilityCannot:
			facts.LostCount++
		case AbilityHelp:
			facts.PartialCount++
		case AbilityUnknown:
			facts.UnknownCount++
		}
	}

	return facts
}

// FactsSummary описывает собранные признаки словами — это то, что видит
// координатор независимо от того, утверждена матрица маршрутов или нет
func FactsSummary(app *models.Application) string {
	facts := CollectFacts(app)

	parts := []string{
		fmt.Sprintf("ограничения самообслуживания: не может — %d из 3, только с помощью — %d из 3, не знаю — %d из 3",
			facts.LostCount, facts.PartialCount, facts.UnknownCount),
		"ухаживающий: " + Label(CaregiverOptions, app.Caregiver),
		"медицинская составляющая со слов заявителя: " + Label(MedicalOptions, app.MedicalNeed),
	}

	if spheres := app.SpheresList(); len(spheres) > 0 {
		labels := make([]string, 0, len(spheres))
		for _, s := range spheres {
			labels = append(labels, Label(SphereOptions, s))
		}
		parts = append(parts, "дополнительные сферы: "+strings.Join(labels, ", "))
	}

	return strings.Join(parts, "; ")
}

// Route применяет утверждённую матрицу маршрутов к обращению.
//
// Если матрица не утверждена или ни одно правило не подошло, предварительное
// направление не рассчитывается: координатор получает только признаки случая.
func Route(app *models.Application, matrix policy.Routing) policy.Decision {
	return matrix.Apply(CollectFacts(app))
}
