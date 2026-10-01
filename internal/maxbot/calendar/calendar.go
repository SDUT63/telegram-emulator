// Package calendar считает сроки в рабочих днях: «один рабочий день» — это
// не «плюс 24 часа», а конец следующего рабочего дня с учётом выходных,
// нерабочих праздничных дней и часового пояса организации.
package calendar

import (
	"fmt"
	"strconv"
	"strings"
	"time"
)

// Calendar рабочий календарь организации
type Calendar struct {
	location *time.Location
	// workingDays отмечает рабочие дни недели
	workingDays map[time.Weekday]bool
	// start и end — границы рабочего дня в минутах от полуночи
	start int
	end   int
	// holidays — нерабочие праздничные дни в формате 2006-01-02
	holidays map[string]bool
}

// Config описание рабочего календаря в конфигурации
type Config struct {
	Timezone string   `mapstructure:"timezone"`
	Days     []string `mapstructure:"days"`
	Start    string   `mapstructure:"start"`
	End      string   `mapstructure:"end"`
	Holidays []string `mapstructure:"holidays"`
}

var weekdayNames = map[string]time.Weekday{
	"mon": time.Monday,
	"tue": time.Tuesday,
	"wed": time.Wednesday,
	"thu": time.Thursday,
	"fri": time.Friday,
	"sat": time.Saturday,
	"sun": time.Sunday,
}

// New создаёт календарь по конфигурации
func New(cfg Config) (*Calendar, error) {
	location := time.Local
	if strings.TrimSpace(cfg.Timezone) != "" {
		loaded, err := time.LoadLocation(cfg.Timezone)
		if err != nil {
			return nil, fmt.Errorf("неизвестный часовой пояс %q: %w", cfg.Timezone, err)
		}
		location = loaded
	}

	days := make(map[time.Weekday]bool, len(cfg.Days))
	for _, d := range cfg.Days {
		weekday, ok := weekdayNames[strings.ToLower(strings.TrimSpace(d))]
		if !ok {
			return nil, fmt.Errorf("неизвестный день недели %q (допустимо mon, tue, wed, thu, fri, sat, sun)", d)
		}
		days[weekday] = true
	}
	if len(days) == 0 {
		for _, d := range []time.Weekday{time.Monday, time.Tuesday, time.Wednesday, time.Thursday, time.Friday} {
			days[d] = true
		}
	}

	start, err := parseClock(cfg.Start, 9*60)
	if err != nil {
		return nil, fmt.Errorf("некорректное начало рабочего дня: %w", err)
	}
	end, err := parseClock(cfg.End, 18*60)
	if err != nil {
		return nil, fmt.Errorf("некорректное окончание рабочего дня: %w", err)
	}
	if end <= start {
		return nil, fmt.Errorf("окончание рабочего дня должно быть позже начала")
	}

	holidays := make(map[string]bool, len(cfg.Holidays))
	for _, h := range cfg.Holidays {
		h = strings.TrimSpace(h)
		if h == "" {
			continue
		}
		if _, err := time.Parse("2006-01-02", h); err != nil {
			return nil, fmt.Errorf("некорректная дата праздничного дня %q: ожидается формат 2006-01-02", h)
		}
		holidays[h] = true
	}

	return &Calendar{
		location:    location,
		workingDays: days,
		start:       start,
		end:         end,
		holidays:    holidays,
	}, nil
}

// Location возвращает часовой пояс календаря
func (c *Calendar) Location() *time.Location {
	return c.location
}

// IsWorkingDay сообщает, является ли день рабочим
func (c *Calendar) IsWorkingDay(t time.Time) bool {
	t = t.In(c.location)
	if c.holidays[t.Format("2006-01-02")] {
		return false
	}

	return c.workingDays[t.Weekday()]
}

// IsWorkingTime сообщает, попадает ли момент в рабочее время
func (c *Calendar) IsWorkingTime(t time.Time) bool {
	if !c.IsWorkingDay(t) {
		return false
	}

	t = t.In(c.location)
	minutes := t.Hour()*60 + t.Minute()

	return minutes >= c.start && minutes < c.end
}

// EndOfWorkingDay возвращает момент окончания рабочего дня для указанной даты
func (c *Calendar) EndOfWorkingDay(t time.Time) time.Time {
	t = t.In(c.location)

	return time.Date(t.Year(), t.Month(), t.Day(), c.end/60, c.end%60, 0, 0, c.location)
}

// AddWorkingDays возвращает дату, отстоящую от указанной на days рабочих дней.
//
// Отсчёт ведётся от рабочего дня: если момент попал на выходной или на время
// после окончания рабочего дня, отсчёт начинается со следующего рабочего дня.
func (c *Calendar) AddWorkingDays(from time.Time, days int) time.Time {
	current := from.In(c.location)

	// Незавершённый рабочий день считается днём отсчёта; иначе переходим к следующему
	if !c.IsWorkingDay(current) || c.afterWorkingHours(current) {
		current = c.nextWorkingDay(current)
		days--
	}

	for ; days > 0; days-- {
		current = c.nextWorkingDay(current)
	}

	return current
}

// NextWorkingDeadline возвращает срок «не более days рабочих дней»:
// конец рабочего дня, на который истекает срок
func (c *Calendar) NextWorkingDeadline(from time.Time, days int) time.Time {
	if days < 1 {
		days = 1
	}

	return c.EndOfWorkingDay(c.AddWorkingDays(from, days))
}

// CalendarDaysDeadline возвращает срок в календарных днях, но не ранее
// окончания minWorkingDays рабочих дней.
//
// Так считается контроль на 7-й день: звонок выполняется на 7-й календарный
// день, но не ранее истечения пятого рабочего дня с даты передачи.
func (c *Calendar) CalendarDaysDeadline(from time.Time, calendarDays, minWorkingDays int) time.Time {
	calendar := c.EndOfWorkingDay(from.In(c.location).AddDate(0, 0, calendarDays))
	if minWorkingDays <= 0 {
		return calendar
	}

	working := c.NextWorkingDeadline(from, minWorkingDays)
	if working.After(calendar) {
		return working
	}

	return calendar
}

// afterWorkingHours сообщает, закончился ли рабочий день
func (c *Calendar) afterWorkingHours(t time.Time) bool {
	t = t.In(c.location)

	return t.Hour()*60+t.Minute() >= c.end
}

// nextWorkingDay возвращает ближайший следующий рабочий день
func (c *Calendar) nextWorkingDay(t time.Time) time.Time {
	next := t.In(c.location).AddDate(0, 0, 1)
	for i := 0; i < 366; i++ {
		if c.IsWorkingDay(next) {
			return next
		}
		next = next.AddDate(0, 0, 1)
	}

	// Календарь без рабочих дней — конфигурация заведомо некорректна
	return next
}

// parseClock разбирает время вида 09:00 в минуты от полуночи
func parseClock(value string, fallback int) (int, error) {
	value = strings.TrimSpace(value)
	if value == "" {
		return fallback, nil
	}

	parts := strings.Split(value, ":")
	if len(parts) != 2 {
		return 0, fmt.Errorf("ожидается формат ЧЧ:ММ, получено %q", value)
	}

	hours, err := strconv.Atoi(parts[0])
	if err != nil || hours < 0 || hours > 23 {
		return 0, fmt.Errorf("некорректные часы в %q", value)
	}
	minutes, err := strconv.Atoi(parts[1])
	if err != nil || minutes < 0 || minutes > 59 {
		return 0, fmt.Errorf("некорректные минуты в %q", value)
	}

	return hours*60 + minutes, nil
}
