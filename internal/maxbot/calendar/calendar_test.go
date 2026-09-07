package calendar

import (
	"testing"
	"time"
)

func testCalendar(t *testing.T) *Calendar {
	t.Helper()

	c, err := New(Config{
		Timezone: "Europe/Samara",
		Days:     []string{"mon", "tue", "wed", "thu", "fri"},
		Start:    "09:00",
		End:      "18:00",
		Holidays: []string{"2026-08-24"},
	})
	if err != nil {
		t.Fatalf("не удалось создать календарь: %v", err)
	}

	return c
}

func at(t *testing.T, c *Calendar, value string) time.Time {
	t.Helper()

	parsed, err := time.ParseInLocation("2006-01-02 15:04", value, c.Location())
	if err != nil {
		t.Fatalf("некорректная дата %q: %v", value, err)
	}

	return parsed
}

func TestNextWorkingDeadline(t *testing.T) {
	c := testCalendar(t)

	cases := []struct {
		name     string
		from     string
		expected string
	}{
		// Среда днём: срок — конец четверга
		{"будний день в рабочее время", "2026-08-19 10:00", "2026-08-20 18:00"},
		// Пятница в рабочее время: следующий рабочий день — понедельник
		{"пятница днём", "2026-08-14 10:00", "2026-08-17 18:00"},
		// Пятница после окончания работы: отсчёт начинается с понедельника
		{"пятница после рабочего дня", "2026-08-14 17:55", "2026-08-17 18:00"},
		// Выходной: отсчёт начинается с понедельника
		{"выходной", "2026-08-15 12:00", "2026-08-17 18:00"},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := c.NextWorkingDeadline(at(t, c, tc.from), 1)
			if want := at(t, c, tc.expected); !got.Equal(want) {
				t.Fatalf("срок рассчитан неверно: получено %s, ожидалось %s",
					got.Format("2006-01-02 15:04 MST"), want.Format("2006-01-02 15:04 MST"))
			}
		})
	}
}

func TestHolidayIsNotWorkingDay(t *testing.T) {
	c := testCalendar(t)

	// 24 августа объявлен нерабочим: срок с пятницы переносится на вторник
	holiday := at(t, c, "2026-08-24 12:00")
	if c.IsWorkingDay(holiday) {
		t.Fatal("праздничный день не является рабочим")
	}

	got := c.NextWorkingDeadline(at(t, c, "2026-08-21 10:00"), 1)
	if want := at(t, c, "2026-08-25 18:00"); !got.Equal(want) {
		t.Fatalf("праздник должен сдвигать срок: получено %s, ожидалось %s",
			got.Format("2006-01-02 15:04"), want.Format("2006-01-02 15:04"))
	}
}

func TestTwentyFourHoursIsNotOneWorkingDay(t *testing.T) {
	c := testCalendar(t)

	from := at(t, c, "2026-08-21 17:55") // пятница, конец рабочего дня
	deadline := c.NextWorkingDeadline(from, 1)

	if !deadline.After(from.Add(24 * time.Hour)) {
		t.Fatalf("срок в один рабочий день не может истекать в выходной: %s",
			deadline.Format("2006-01-02 15:04"))
	}
	if c.IsWorkingDay(from.Add(24 * time.Hour)) {
		t.Fatal("контрольная проверка: суббота не рабочий день")
	}
}

func TestIsWorkingTime(t *testing.T) {
	c := testCalendar(t)

	cases := map[string]bool{
		"2026-08-19 08:59": false,
		"2026-08-19 09:00": true,
		"2026-08-19 17:59": true,
		"2026-08-19 18:00": false,
		"2026-08-22 12:00": false,
	}

	for value, expected := range cases {
		if got := c.IsWorkingTime(at(t, c, value)); got != expected {
			t.Fatalf("IsWorkingTime(%s) = %v, ожидалось %v", value, got, expected)
		}
	}
}

func TestCalendarDaysDeadlineNotEarlierThanWorkingDays(t *testing.T) {
	c := testCalendar(t)

	// Контроль на 7-й календарный день, но не ранее пятого рабочего дня.
	// Передача в пятницу 21.08: 7 календарных дней — 28.08, пять рабочих дней
	// с учётом праздника 24.08 — 28.08. Срок не может оказаться раньше.
	from := at(t, c, "2026-08-21 10:00")
	got := c.CalendarDaysDeadline(from, 7, 5)

	if got.Before(at(t, c, "2026-08-28 18:00")) {
		t.Fatalf("контроль не может назначаться раньше пятого рабочего дня: %s",
			got.Format("2006-01-02 15:04"))
	}
	if !c.IsWorkingDay(got) {
		t.Fatalf("контрольный срок должен приходиться на рабочий день: %s",
			got.Format("2006-01-02 15:04"))
	}
}

func TestInvalidConfig(t *testing.T) {
	cases := map[string]Config{
		"неизвестный часовой пояс": {Timezone: "Mars/Olympus"},
		"неизвестный день недели":  {Days: []string{"monday"}},
		"конец раньше начала":      {Start: "18:00", End: "09:00"},
		"некорректный праздник":    {Holidays: []string{"01.01.2026"}},
	}

	for name, cfg := range cases {
		t.Run(name, func(t *testing.T) {
			if _, err := New(cfg); err == nil {
				t.Fatal("ожидалась ошибка конфигурации календаря")
			}
		})
	}
}
