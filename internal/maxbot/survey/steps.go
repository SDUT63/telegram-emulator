package survey

import (
	"strconv"
	"strings"

	"telegram-emulator/internal/maxbot/models"
)

// buildSteps описывает последовательность шагов анкеты.
//
// Порядок соответствует алгоритму оператора: сначала фильтр STOP-1 (фаза 7.2),
// затем согласие на обработку данных (фаза 7.5), затем остальные сведения.
func (e *Engine) buildSteps() []step {
	return []step{
		e.stepStop1(),
		e.stepStop1Name(),
		e.stepStop1Phone(),
		e.stepConsent(),
		e.stepApplicantName(),
		e.stepPhone(),
		e.stepRelation(),
		e.stepWardName(),
		e.stepWardAge(),
		e.stepWardKnows(),
		e.stepDistrict(),
		e.stepAddress(),
		e.stepIssue(),
		e.stepMobility(),
		e.stepHygiene(),
		e.stepFood(),
		e.stepCaregiver(),
		e.stepCaregiverLoad(),
		e.stepMedical(),
		e.stepSocial(),
		e.stepPrevRequests(),
		e.stepSpheres(),
		e.stepContactPerson(),
		e.stepContactName(),
		e.stepContactTime(),
		e.stepConfirm(),
	}
}

// stepStop1 проходит закрытый перечень признаков экстренного состояния.
// Оператор (и бот) не оценивает состояние здоровья, а сверяет ответ с перечнем.
func (e *Engine) stepStop1() step {
	return step{
		state:   StateStop1,
		kind:    KindChoice,
		text:    fmtStop1Question,
		hint:    "Это обязательная проверка безопасности. Мы задаём вопросы дословно и ничего не оцениваем сами.",
		options: func(Params) []Option { return Stop1Options },
		accept: func(app *models.Application, in Input, p Params) Outcome {
			value, ok := matchOption(Stop1Options, in)
			if !ok {
				return Outcome{Error: "Ответьте, пожалуйста, «Да» или «Нет»."}
			}

			if value == AnswerYes {
				sign := ""
				if app.Stop1Index >= 0 && app.Stop1Index < len(p.Stop1Signs) {
					sign = p.Stop1Signs[app.Stop1Index].Question
				}
				app.Stop1Mark = Stop1Mark
				app.Stop1Sign = sign
				app.Stop1At = now()

				return Outcome{Accepted: true, Stop1: true}
			}

			app.Stop1Index++
			if app.Stop1Index >= len(p.Stop1Signs) {
				// Ни один признак не выявлен — формальный допуск в навигацию
				app.Stop1Mark = Stop2Mark
				app.State = e.advanceFrom(app, e.index[StateStop1])
			}

			return Outcome{Accepted: true}
		},
	}
}

func (e *Engine) stepStop1Name() step {
	return step{
		state: StateStop1Name,
		kind:  KindText,
		text: func(*models.Application, Params) string {
			return "Чтобы координатор связался с вами завтра, напишите, пожалуйста, ваше имя."
		},
		accept: textStep(2, "Напишите, пожалуйста, имя текстом.", func(app *models.Application, v string) {
			app.ApplicantName = v
		}),
	}
}

func (e *Engine) stepStop1Phone() step {
	return step{
		state:   StateStop1Phone,
		kind:    KindPhone,
		contact: true,
		text: func(*models.Application, Params) string {
			return "Оставьте, пожалуйста, номер телефона для связи."
		},
		accept: acceptPhone,
	}
}

func (e *Engine) stepConsent() step {
	return step{
		state: StateConsent,
		kind:  KindChoice,
		text: func(*models.Application, Params) string {
			return "Чтобы мы могли работать с вашим обращением, нужно ваше согласие на обработку данных: " +
				"имя, контакт, адрес и то, что вы расскажете о состоянии здоровья.\n\n" +
				"Данные нужны только для того, чтобы определить, куда направить обращение. " +
				"Согласие можно отозвать в любой момент — тогда запись будет удалена.\n\n" +
				"Вы согласны?"
		},
		hint:    "Согласие на передачу сведений в конкретную организацию оформляется отдельно — когда координатор назовёт эту организацию.",
		options: func(Params) []Option { return ConsentOptions },
		accept: func(app *models.Application, in Input, _ Params) Outcome {
			value, ok := matchOption(ConsentOptions, in)
			if !ok {
				return Outcome{Error: "Выберите, пожалуйста, один из вариантов кнопкой."}
			}
			if value == AnswerNo {
				return Outcome{Accepted: true, Declined: true}
			}
			app.ConsentPD = true
			app.ConsentPDAt = now()

			return Outcome{Accepted: true}
		},
	}
}

func (e *Engine) stepApplicantName() step {
	return step{
		state: StateApplicantName,
		kind:  KindText,
		text: func(*models.Application, Params) string {
			return "Как вас зовут? Напишите, пожалуйста, фамилию, имя и отчество."
		},
		accept: textStep(2, "Напишите, пожалуйста, ваше имя текстом.", func(app *models.Application, v string) {
			app.ApplicantName = v
		}),
	}
}

func (e *Engine) stepPhone() step {
	return step{
		state:   StatePhone,
		kind:    KindPhone,
		contact: true,
		text: func(*models.Application, Params) string {
			return "По какому номеру телефона с вами связаться?"
		},
		hint:   "Можно нажать кнопку и отправить свой номер или написать его текстом.",
		accept: acceptPhone,
	}
}

func acceptPhone(app *models.Application, in Input, _ Params) Outcome {
	raw := in.Phone
	if strings.TrimSpace(raw) == "" {
		raw = in.Text
	}
	phone, ok := NormalizePhone(raw)
	if !ok {
		return Outcome{Error: "Не получилось распознать номер. Напишите его в формате +7 900 000-00-00."}
	}
	app.ApplicantPhone = phone

	return Outcome{Accepted: true}
}

func (e *Engine) stepRelation() step {
	return step{
		state: StateRelation,
		kind:  KindChoice,
		text: func(*models.Application, Params) string {
			return "Кем вы приходитесь человеку, о котором идёт речь?"
		},
		hint:    "Обратиться может кто угодно: родственник, сосед, специалист. Мы не отказываем в приёме обращения.",
		options: func(Params) []Option { return RelationOptions },
		accept: choice(RelationOptions, func(app *models.Application, v string) {
			app.Relation = v
			if v == RelationSelf {
				app.WardName = app.ApplicantName
				app.WardKnows = AnswerYes
			}
		}),
	}
}

func (e *Engine) stepWardName() step {
	return step{
		state:    StateWardName,
		kind:     KindText,
		skipWhen: func(app *models.Application) bool { return app.Relation == RelationSelf },
		text: func(*models.Application, Params) string {
			return "Как зовут человека, которому нужна помощь? Фамилия, имя и отчество."
		},
		accept: textStep(2, "Напишите, пожалуйста, имя человека текстом.", func(app *models.Application, v string) {
			app.WardName = v
		}),
	}
}

func (e *Engine) stepWardAge() step {
	return step{
		state: StateWardAge,
		kind:  KindAge,
		text: func(app *models.Application, _ Params) string {
			if app.Relation == RelationSelf {
				return "Сколько вам полных лет?"
			}

			return "Сколько ему или ей полных лет?"
		},
		accept: func(app *models.Application, in Input, _ Params) Outcome {
			value := strings.TrimSpace(in.Text)
			age, err := strconv.Atoi(value)
			if err != nil || age < 0 || age > 120 {
				return Outcome{Error: "Напишите возраст числом, например: 78."}
			}
			app.WardAge = age

			return Outcome{Accepted: true}
		},
	}
}

func (e *Engine) stepWardKnows() step {
	return step{
		state:    StateWardKnows,
		kind:     KindChoice,
		skipWhen: func(app *models.Application) bool { return app.Relation == RelationSelf },
		text: func(*models.Application, Params) string {
			return "Знает ли человек о вашем обращении к нам?"
		},
		hint:    "Это нужно, чтобы при контрольном звонке не поставить его в неловкое положение.",
		options: func(Params) []Option { return KnowsOptions },
		accept: choice(KnowsOptions, func(app *models.Application, v string) {
			app.WardKnows = v
		}),
	}
}

func (e *Engine) stepDistrict() step {
	return step{
		state: StateDistrict,
		kind:  KindChoice,
		text: func(*models.Application, Params) string {
			return "В каком районе живёт человек, которому нужна помощь?"
		},
		options: districtOptions,
		accept: func(app *models.Application, in Input, p Params) Outcome {
			options := districtOptions(p)
			value, ok := matchOption(options, in)
			if !ok {
				return Outcome{Error: "Выберите, пожалуйста, район кнопкой."}
			}
			app.District = Label(options, value)

			return Outcome{Accepted: true}
		},
	}
}

func (e *Engine) stepAddress() step {
	return step{
		state: StateAddress,
		kind:  KindText,
		text: func(*models.Application, Params) string {
			return "Напишите адрес: улица, дом, квартира, этаж и код домофона, если он есть."
		},
		hint: "Адрес нужен, чтобы выбрать организацию по территории.",
		accept: textStep(5, "Напишите адрес чуть подробнее: улица и дом.", func(app *models.Application, v string) {
			app.Address = v
		}),
	}
}

func (e *Engine) stepIssue() step {
	return step{
		state: StateIssue,
		kind:  KindText,
		text: func(*models.Application, Params) string {
			return "Расскажите своими словами, что произошло и с чем сейчас тяжелее всего справляться."
		},
		hint: "Диагноз, номера документов и сведения о доходах указывать не нужно — они не влияют на выбор маршрута, и мы их не собираем.",
		accept: textStep(10, "Опишите ситуацию чуть подробнее — хотя бы одним-двумя предложениями.", func(app *models.Application, v string) {
			app.Issue = v
		}),
	}
}

func (e *Engine) stepMobility() step {
	return step{
		state: StateMobility,
		kind:  KindChoice,
		text: func(*models.Application, Params) string {
			return "Человек может сам встать и пройти по квартире?"
		},
		options: func(Params) []Option { return AbilityOptions },
		accept: choice(AbilityOptions, func(app *models.Application, v string) {
			app.Mobility = v
		}),
	}
}

func (e *Engine) stepHygiene() step {
	return step{
		state: StateHygiene,
		kind:  KindChoice,
		text: func(*models.Application, Params) string {
			return "Может помыться самостоятельно?"
		},
		options: func(Params) []Option { return AbilityOptions },
		accept: choice(AbilityOptions, func(app *models.Application, v string) {
			app.Hygiene = v
		}),
	}
}

func (e *Engine) stepFood() step {
	return step{
		state: StateFood,
		kind:  KindChoice,
		text: func(*models.Application, Params) string {
			return "Может приготовить и принять пищу?"
		},
		options: func(Params) []Option { return AbilityOptions },
		accept: choice(AbilityOptions, func(app *models.Application, v string) {
			app.Food = v
		}),
	}
}

func (e *Engine) stepCaregiver() step {
	return step{
		state: StateCaregiver,
		kind:  KindChoice,
		text: func(*models.Application, Params) string {
			return "Есть ли рядом кто-то, кто помогает каждый день?"
		},
		options: func(Params) []Option { return CaregiverOptions },
		accept: choice(CaregiverOptions, func(app *models.Application, v string) {
			app.Caregiver = v
		}),
	}
}

func (e *Engine) stepCaregiverLoad() step {
	return step{
		state: StateCaregiverLoad,
		kind:  KindChoice,
		skipWhen: func(app *models.Application) bool {
			return app.Caregiver == CaregiverNone || app.Caregiver == CaregiverUnknown
		},
		text: func(*models.Application, Params) string {
			return "Сколько времени уходит на уход каждый день у того, кто помогает?"
		},
		hint:    "Это помогает понять, насколько тяжело ухаживающему.",
		options: func(Params) []Option { return CaregiverLoadOptions },
		accept: choice(CaregiverLoadOptions, func(app *models.Application, v string) {
			app.CaregiverLoad = v
		}),
	}
}

func (e *Engine) stepMedical() step {
	return step{
		state: StateMedical,
		kind:  KindChoice,
		text: func(*models.Application, Params) string {
			return "Есть ли сейчас боль, которую не удаётся снять, или нужен медицинский контроль на дому?"
		},
		hint:    "Ответьте так, как видите ситуацию сами. Мы не спрашиваем диагноз и не оцениваем состояние.",
		options: func(Params) []Option { return MedicalOptions },
		accept: choice(MedicalOptions, func(app *models.Application, v string) {
			app.MedicalNeed = v
		}),
	}
}

func (e *Engine) stepSocial() step {
	return step{
		state: StateSocial,
		kind:  KindChoice,
		text: func(*models.Application, Params) string {
			return "Получает ли человек сейчас какие-нибудь социальные услуги: соцработник, надомное обслуживание?"
		},
		options: func(Params) []Option { return SocialOptions },
		accept: choice(SocialOptions, func(app *models.Application, v string) {
			app.SocialServices = v
		}),
	}
}

func (e *Engine) stepPrevRequests() step {
	return step{
		state: StatePrevRequests,
		kind:  KindText,
		skip:  true,
		text: func(*models.Application, Params) string {
			return "Куда вы уже обращались и что вам ответили?"
		},
		hint: "Если никуда не обращались — нажмите «Пропустить».",
		accept: func(app *models.Application, in Input, _ Params) Outcome {
			if strings.TrimSpace(in.Payload) == "skip" {
				app.PreviousRequests = "не обращались"

				return Outcome{Accepted: true}
			}

			return textStep(2, "Напишите коротко, куда обращались, или нажмите «Пропустить».",
				func(app *models.Application, v string) { app.PreviousRequests = v })(app, in, Params{})
		},
	}
}

func (e *Engine) stepSpheres() step {
	return step{
		state: StateSpheres,
		kind:  KindMulti,
		text: func(*models.Application, Params) string {
			return "Есть ли ещё вопросы, кроме ухода? Отметьте всё, что подходит, и нажмите «Готово»."
		},
		options: func(Params) []Option { return SphereOptions },
		accept: func(app *models.Application, in Input, _ Params) Outcome {
			payload := strings.TrimSpace(in.Payload)
			switch payload {
			case "done":
				app.State = e.advanceFrom(app, e.index[StateSpheres])

				return Outcome{Accepted: true}
			case SphereNone:
				app.OtherSpheres = ""
				app.State = e.advanceFrom(app, e.index[StateSpheres])

				return Outcome{Accepted: true}
			}

			value, ok := matchOption(SphereOptions, in)
			if !ok {
				return Outcome{Error: "Отметьте варианты кнопками, затем нажмите «Готово»."}
			}
			app.ToggleSphere(value)

			return Outcome{Accepted: true}
		},
	}
}

func (e *Engine) stepContactPerson() step {
	return step{
		state: StateContactPerson,
		kind:  KindChoice,
		text: func(*models.Application, Params) string {
			return "Кто будет с нами на связи, когда мы позвоним через неделю и спросим, началась ли помощь?"
		},
		options: func(Params) []Option { return ContactPersonOptions },
		accept: choice(ContactPersonOptions, func(app *models.Application, v string) {
			if v == "self" {
				app.ContactPerson = "заявитель"
			} else {
				app.ContactPerson = ""
			}
		}),
	}
}

func (e *Engine) stepContactName() step {
	return step{
		state:    StateContactName,
		kind:     KindText,
		skipWhen: func(app *models.Application) bool { return app.ContactPerson == "заявитель" },
		text: func(*models.Application, Params) string {
			return "Напишите, пожалуйста, имя и телефон человека, который будет на связи."
		},
		accept: textStep(3, "Напишите имя и телефон одним сообщением.", func(app *models.Application, v string) {
			app.ContactPerson = v
		}),
	}
}

func (e *Engine) stepContactTime() step {
	return step{
		state: StateContactTime,
		kind:  KindChoice,
		text: func(*models.Application, Params) string {
			return "В какое время вам удобно принять звонок?"
		},
		options: func(Params) []Option { return ContactTimeOptions },
		accept: choice(ContactTimeOptions, func(app *models.Application, v string) {
			app.ContactTime = v
		}),
	}
}

func (e *Engine) stepConfirm() step {
	return step{
		state: StateConfirm,
		kind:  KindChoice,
		text: func(*models.Application, Params) string {
			return "Если всё верно — нажмите «Отправить обращение». Если где-то ошибка — «Заполнить заново»."
		},
		options: func(Params) []Option { return ConfirmOptions },
		accept: func(app *models.Application, in Input, _ Params) Outcome {
			value, ok := matchOption(ConfirmOptions, in)
			if !ok {
				return Outcome{Error: "Выберите «Отправить обращение» или «Заполнить заново»."}
			}
			if value == "restart" {
				return Outcome{Accepted: true, Restart: true}
			}

			return Outcome{Accepted: true, Completed: true}
		},
	}
}
