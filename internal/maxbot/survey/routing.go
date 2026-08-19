package survey

import (
	"fmt"
	"strings"

	"telegram-emulator/internal/maxbot/models"
)

// Коды маршрутов матрицы М1–М5 (приложение 3 методических рекомендаций)
const (
	RouteM1 = "М1"
	RouteM2 = "М2"
	RouteM3 = "М3"
	RouteM4 = "М4"
	RouteM5 = "М5"
)

// RouteDescriptions расшифровка маршрутов для карточки координатора
var RouteDescriptions = map[string]string{
	RouteM1: "медицинский контур — отделение паллиативной медицинской помощи",
	RouteM2: "социальный контур — КЦСОН по району",
	RouteM3: "контур долговременного ухода — ТКЦ СДУ либо уполномоченный орган",
	RouteM4: "спорный случай — решение эксперта по нуждаемости",
	RouteM5: "межведомственный контур — несколько получателей",
}

// Route возвращает предварительный маршрут и обоснование выбора.
//
// Результат — подсказка координатору, а не решение: маршрут при сомнении
// определяет координатор, а уровень нуждаемости устанавливает эксперт.
func Route(app *models.Application) (string, string) {
	lost := 0
	partial := 0
	unknown := 0
	for _, v := range []string{app.Mobility, app.Hygiene, app.Food} {
		switch v {
		case AbilityCannot:
			lost++
		case AbilityHelp:
			partial++
		case AbilityUnknown:
			unknown++
		}
	}

	spheres := app.SpheresList()
	reasons := []string{
		fmt.Sprintf("ограничения самообслуживания: не может — %d из 3, только с помощью — %d из 3", lost, partial),
		"ухаживающий: " + Label(CaregiverOptions, app.Caregiver),
		"медицинская составляющая со слов заявителя: " + Label(MedicalOptions, app.MedicalNeed),
	}
	if len(spheres) > 0 {
		labels := make([]string, 0, len(spheres))
		for _, s := range spheres {
			labels = append(labels, Label(SphereOptions, s))
		}
		reasons = append(reasons, "дополнительные сферы: "+strings.Join(labels, ", "))
	}

	reason := func(code, why string) (string, string) {
		return code, why + ". Признаки: " + strings.Join(reasons, "; ") +
			". Маршрут предварительный, решение принимает координатор"
	}

	// Картина неясна — спорный случай, решение принимает эксперт
	if unknown > 0 || app.Caregiver == CaregiverUnknown || app.MedicalNeed == AnswerUnknown {
		return reason(RouteM4, "заявитель не смог ответить на часть вопросов, картина неясна")
	}

	// Потребность охватывает две и более сферы одновременно
	if len(spheres) > 0 && (lost+partial > 0 || app.MedicalNeed == AnswerYes) {
		return reason(RouteM5, "потребность охватывает две и более сферы одновременно")
	}

	if app.MedicalNeed == AnswerYes {
		return reason(RouteM1, "со слов заявителя есть боль, которая не снимается, либо нужен медицинский контроль на дому")
	}

	if lost >= 3 {
		return reason(RouteM3, "значительная утрата самообслуживания по трём позициям")
	}

	if lost+partial > 0 {
		if app.Caregiver == CaregiverDaily || app.Caregiver == CaregiverSometimes {
			return reason(RouteM2, "частичная утрата самообслуживания, ухаживающий есть, медицинский контроль не требуется")
		}

		return reason(RouteM4, "утрата самообслуживания при отсутствии ухаживающего — уровень нуждаемости определяет эксперт")
	}

	return reason(RouteM4, "признаков ограничений самообслуживания не зафиксировано, потребность требует уточнения")
}
