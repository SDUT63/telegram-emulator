// Package config содержит конфигурацию чат-бота «Точка входа» для мессенджера MAX.
package config

import (
	"fmt"
	"strings"
	"time"

	"telegram-emulator/internal/maxbot/calendar"

	"github.com/spf13/viper"
)

// Config представляет конфигурацию чат-бота
type Config struct {
	Bot          BotConfig          `mapstructure:"bot"`
	Database     DatabaseConfig     `mapstructure:"database"`
	Organization OrganizationConfig `mapstructure:"organization"`
	Coordinator  CoordinatorConfig  `mapstructure:"coordinator"`
	Access       AccessConfig       `mapstructure:"access"`
	Intake       IntakeConfig       `mapstructure:"intake"`
	Calendar     calendar.Config    `mapstructure:"working_calendar"`
	Policies     PoliciesConfig     `mapstructure:"policies"`
	Consent      ConsentConfig      `mapstructure:"consent"`
	Logging      LoggingConfig      `mapstructure:"logging"`
}

// PoliciesConfig путь к файлу утверждаемых правил
type PoliciesConfig struct {
	Path string `mapstructure:"path"`
}

// ConsentConfig формулировка согласия на обработку персональных данных.
//
// Текст задаёт организация: он должен соответствовать утверждённому скрипту
// и политике обработки данных. Бот не сочиняет юридические формулировки
// и не обещает удаление записей при отзыве согласия.
type ConsentConfig struct {
	Text string `mapstructure:"text"`
}

// Роли доступа к сведениям об обращениях
const (
	RoleNone        = ""
	RoleViewer      = "viewer"
	RoleCoordinator = "coordinator"
	RoleSupervisor  = "supervisor"
	RoleAdmin       = "admin"
)

// AccessConfig распределение ролей по идентификаторам пользователей MAX
type AccessConfig struct {
	Viewers      []int64 `mapstructure:"viewers"`
	Coordinators []int64 `mapstructure:"coordinators"`
	Supervisors  []int64 `mapstructure:"supervisors"`
	Admins       []int64 `mapstructure:"admins"`
}

// BotConfig параметры подключения к MAX Bot API
type BotConfig struct {
	Token   string        `mapstructure:"token"`
	APIURL  string        `mapstructure:"api_url"`
	Mode    string        `mapstructure:"mode"` // polling | webhook
	Timeout string        `mapstructure:"timeout"`
	Debug   bool          `mapstructure:"debug"`
	Webhook WebhookConfig `mapstructure:"webhook"`
}

// WebhookConfig параметры режима webhook
type WebhookConfig struct {
	URL    string `mapstructure:"url"`
	Listen string `mapstructure:"listen"`
	Path   string `mapstructure:"path"`
	Secret string `mapstructure:"secret"`
}

// DatabaseConfig конфигурация базы данных обращений
type DatabaseConfig struct {
	URL string `mapstructure:"url"`
}

// OrganizationConfig сведения о службе, которые бот называет заявителю
type OrganizationConfig struct {
	Name        string `mapstructure:"name"`
	ServiceName string `mapstructure:"service_name"`
	Phone       string `mapstructure:"phone"`
	WorkHours   string `mapstructure:"work_hours"`
	PublicURL   string `mapstructure:"public_url"`
	PolicyURL   string `mapstructure:"policy_url"`
}

// CoordinatorConfig получатели карточек обращений внутри MAX
type CoordinatorConfig struct {
	ChatID  int64   `mapstructure:"chat_id"`
	UserIDs []int64 `mapstructure:"user_ids"`
}

// IntakeConfig параметры анкеты первичного обращения и контрольных точек
type IntakeConfig struct {
	Districts []string `mapstructure:"districts"`
	// NextStepWorkingDays — срок нашего действия в рабочих днях
	NextStepWorkingDays int `mapstructure:"next_step_working_days"`
	// FollowUp7Days и FollowUp30Days — контрольные точки в календарных днях
	FollowUp7Days int `mapstructure:"follow_up_7_days"`
	// FollowUpMinWorkingDays — контроль не раньше истечения норматива результата
	FollowUpMinWorkingDays int `mapstructure:"follow_up_min_working_days"`
	FollowUp30Days         int `mapstructure:"follow_up_30_days"`
	// ReminderInterval — как часто бот напоминает координатору о наступивших сроках
	ReminderInterval string `mapstructure:"reminder_interval"`
}

// LoggingConfig конфигурация логирования
type LoggingConfig struct {
	Level  string `mapstructure:"level"`
	Format string `mapstructure:"format"`
	File   string `mapstructure:"file"`
}

// Load загружает конфигурацию из файла и переменных окружения
func Load() (*Config, error) {
	v := viper.New()
	v.SetConfigName("maxbot")
	v.SetConfigType("yaml")
	v.AddConfigPath("./configs")
	v.AddConfigPath(".")

	v.SetEnvPrefix("MAXBOT")
	v.SetEnvKeyReplacer(strings.NewReplacer(".", "_"))
	v.AutomaticEnv()

	setDefaults(v)

	// Токен всегда читается из окружения, даже если файла конфигурации нет
	if err := v.BindEnv("bot.token", "MAXBOT_BOT_TOKEN"); err != nil {
		return nil, fmt.Errorf("ошибка привязки переменной окружения: %w", err)
	}

	if err := v.ReadInConfig(); err != nil {
		if _, ok := err.(viper.ConfigFileNotFoundError); !ok {
			return nil, fmt.Errorf("ошибка чтения конфигурационного файла: %w", err)
		}
	}

	var cfg Config
	if err := v.Unmarshal(&cfg); err != nil {
		return nil, fmt.Errorf("ошибка парсинга конфигурации: %w", err)
	}

	return &cfg, nil
}

// setDefaults устанавливает значения по умолчанию
func setDefaults(v *viper.Viper) {
	v.SetDefault("bot.token", "")
	v.SetDefault("bot.api_url", "")
	v.SetDefault("bot.mode", "polling")
	v.SetDefault("bot.timeout", "30s")
	v.SetDefault("bot.debug", false)
	v.SetDefault("bot.webhook.listen", ":10888")
	v.SetDefault("bot.webhook.path", "/webhook")

	v.SetDefault("database.url", "sqlite://data/maxbot.db")

	v.SetDefault("organization.name", "АНО «Система долговременного ухода Тольятти»")
	v.SetDefault("organization.service_name", "Точка входа")
	v.SetDefault("organization.work_hours", "по будням с 9:00 до 18:00")

	v.SetDefault("policies.path", "configs/policies.yaml")

	v.SetDefault("consent.text", defaultConsentText)

	v.SetDefault("working_calendar.timezone", "Europe/Samara")
	v.SetDefault("working_calendar.days", []string{"mon", "tue", "wed", "thu", "fri"})
	v.SetDefault("working_calendar.start", "09:00")
	v.SetDefault("working_calendar.end", "18:00")

	v.SetDefault("intake.next_step_working_days", 1)
	v.SetDefault("intake.follow_up_7_days", 7)
	v.SetDefault("intake.follow_up_min_working_days", 5)
	v.SetDefault("intake.follow_up_30_days", 30)
	v.SetDefault("intake.reminder_interval", "30m")
	v.SetDefault("intake.districts", []string{
		"Автозаводский район",
		"Центральный район",
		"Комсомольский район",
	})

	v.SetDefault("logging.level", "info")
	v.SetDefault("logging.format", "console")
	v.SetDefault("logging.file", "")
}

// Validate проверяет, что конфигурации достаточно для запуска бота
func (c *Config) Validate() error {
	if strings.TrimSpace(c.Bot.Token) == "" {
		return fmt.Errorf("не задан токен бота: укажите bot.token или переменную окружения MAXBOT_BOT_TOKEN")
	}

	switch c.Bot.Mode {
	case "polling":
	case "webhook":
		if strings.TrimSpace(c.Bot.Webhook.URL) == "" {
			return fmt.Errorf("для режима webhook требуется bot.webhook.url")
		}
	default:
		return fmt.Errorf("неизвестный режим работы бота: %q (допустимо polling или webhook)", c.Bot.Mode)
	}

	if strings.TrimSpace(c.Policies.Path) == "" {
		return fmt.Errorf("не указан путь к файлу правил: policies.path")
	}

	if _, err := c.WorkingCalendar(); err != nil {
		return fmt.Errorf("некорректный рабочий календарь: %w", err)
	}

	if strings.TrimSpace(c.Consent.Text) == "" {
		return fmt.Errorf("не задана формулировка согласия на обработку данных: consent.text")
	}

	if _, err := c.ReminderInterval(); err != nil {
		return fmt.Errorf("некорректный интервал напоминаний: %w", err)
	}

	return nil
}

// WorkingCalendar собирает рабочий календарь организации
func (c *Config) WorkingCalendar() (*calendar.Calendar, error) {
	return calendar.New(c.Calendar)
}

// ReminderInterval возвращает интервал напоминаний координатору
func (c *Config) ReminderInterval() (time.Duration, error) {
	if strings.TrimSpace(c.Intake.ReminderInterval) == "" {
		return 30 * time.Minute, nil
	}

	return time.ParseDuration(c.Intake.ReminderInterval)
}

// Role возвращает роль пользователя: чем выше роль, тем больше доступ
func (c *Config) Role(userID int64) string {
	switch {
	case containsID(c.Access.Admins, userID):
		return RoleAdmin
	case containsID(c.Access.Supervisors, userID):
		return RoleSupervisor
	case containsID(c.Access.Coordinators, userID), containsID(c.Coordinator.UserIDs, userID):
		return RoleCoordinator
	case containsID(c.Access.Viewers, userID):
		return RoleViewer
	}

	return RoleNone
}

// CanSeeFullCard сообщает, вправе ли роль видеть карточку целиком
func CanSeeFullCard(role string) bool {
	switch role {
	case RoleCoordinator, RoleSupervisor, RoleAdmin:
		return true
	}

	return false
}

// CanSeeCases сообщает, вправе ли роль видеть сведения об обращениях
func CanSeeCases(role string) bool {
	return role != RoleNone
}

// CanCompleteActions сообщает, вправе ли роль закрывать действия по случаю
func CanCompleteActions(role string) bool {
	switch role {
	case RoleCoordinator, RoleSupervisor, RoleAdmin:
		return true
	}

	return false
}

func containsID(ids []int64, id int64) bool {
	for _, item := range ids {
		if item == id {
			return true
		}
	}

	return false
}

// GetTimeout возвращает таймаут запросов к MAX Bot API
func (c *Config) GetTimeout() (time.Duration, error) {
	if strings.TrimSpace(c.Bot.Timeout) == "" {
		return 30 * time.Second, nil
	}

	return time.ParseDuration(c.Bot.Timeout)
}

// IsCoordinator сообщает, вправе ли пользователь работать с обращениями
func (c *Config) IsCoordinator(userID int64) bool {
	return CanSeeFullCard(c.Role(userID))
}

// defaultConsentText формулировка согласия по умолчанию.
//
// Она намеренно не обещает удаление записи при отзыве согласия: порядок
// отзыва, основания и сроки хранения определяет политика обработки данных
// организации. Замените текст на согласованный с вашим юристом.
const defaultConsentText = "Чтобы мы могли работать с вашим обращением, нужно ваше согласие на обработку данных: " +
	"имя, контакт, адрес и то, что вы расскажете о состоянии здоровья.\n\n" +
	"Данные нужны только для того, чтобы определить, куда направить обращение. " +
	"Отозвать согласие можно в любой момент — порядок отзыва, сроки хранения и основания " +
	"обработки указаны в политике обработки персональных данных.\n\n" +
	"Вы согласны?"
