package storage

import (
	"os"
	"sync"
	"testing"
	"time"

	"telegram-emulator/internal/maxbot/models"

	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
	gormlogger "gorm.io/gorm/logger"
)

func newTestStorage(t *testing.T) *Storage {
	t.Helper()

	db, err := gorm.Open(sqlite.Open("file::memory:"), &gorm.Config{
		Logger: gormlogger.Default.LogMode(gormlogger.Silent),
	})
	if err != nil {
		t.Fatalf("не удалось открыть тестовую базу: %v", err)
	}

	store, err := New(db)
	if err != nil {
		t.Fatalf("не удалось подготовить базу: %v", err)
	}
	t.Cleanup(func() {
		if err := store.Close(); err != nil {
			t.Logf("ошибка закрытия базы: %v", err)
		}
	})

	return store
}

func TestDraftLifecycle(t *testing.T) {
	store := newTestStorage(t)

	draft, err := store.Draft(42)
	if err != nil {
		t.Fatalf("ошибка поиска черновика: %v", err)
	}
	if draft != nil {
		t.Fatal("у нового пользователя черновика нет")
	}

	app := &models.Application{UserID: 42, Channel: "max", Status: models.StatusDraft, State: "consent"}
	if err := store.Create(app); err != nil {
		t.Fatalf("ошибка создания обращения: %v", err)
	}

	draft, err = store.Draft(42)
	if err != nil {
		t.Fatalf("ошибка поиска черновика: %v", err)
	}
	if draft == nil || draft.ID != app.ID {
		t.Fatal("черновик должен находиться по идентификатору пользователя")
	}

	app.Status = models.StatusSubmitted
	app.PublicID = "MAX-20260819-0001"
	if err := store.Save(app); err != nil {
		t.Fatalf("ошибка сохранения обращения: %v", err)
	}

	draft, err = store.Draft(42)
	if err != nil {
		t.Fatalf("ошибка поиска черновика: %v", err)
	}
	if draft != nil {
		t.Fatal("после отправки черновик не возвращается")
	}

	found, err := store.ByPublicID("MAX-20260819-0001")
	if err != nil {
		t.Fatalf("ошибка поиска по номеру: %v", err)
	}
	if found == nil {
		t.Fatal("обращение должно находиться по публичному номеру")
	}

	previous, err := store.PreviousApplications(42, 5)
	if err != nil {
		t.Fatalf("ошибка выборки прошлых обращений: %v", err)
	}
	if len(previous) != 1 {
		t.Fatalf("прошлые обращения должны находиться: получено %d", len(previous))
	}
}

func TestNextPublicIDIncrementsWithinDay(t *testing.T) {
	store := newTestStorage(t)
	day := time.Date(2026, 8, 19, 10, 0, 0, 0, time.UTC)

	first, err := store.NextPublicID(day)
	if err != nil {
		t.Fatalf("ошибка нумерации: %v", err)
	}
	if first != "MAX-20260819-0001" {
		t.Fatalf("неверный номер первого обращения: %q", first)
	}

	if err := store.Create(&models.Application{UserID: 1, PublicID: first, Status: models.StatusSubmitted}); err != nil {
		t.Fatalf("ошибка создания обращения: %v", err)
	}

	second, err := store.NextPublicID(day)
	if err != nil {
		t.Fatalf("ошибка нумерации: %v", err)
	}
	if second != "MAX-20260819-0002" {
		t.Fatalf("номер должен увеличиваться в пределах дня, получено %q", second)
	}

	nextDay, err := store.NextPublicID(day.AddDate(0, 0, 1))
	if err != nil {
		t.Fatalf("ошибка нумерации: %v", err)
	}
	if nextDay != "MAX-20260820-0001" {
		t.Fatalf("нумерация должна начинаться заново каждый день, получено %q", nextDay)
	}
}

func TestEventsJournal(t *testing.T) {
	store := newTestStorage(t)

	app := &models.Application{UserID: 7, Status: models.StatusDraft}
	if err := store.Create(app); err != nil {
		t.Fatalf("ошибка создания обращения: %v", err)
	}

	if err := store.LogEvent(app.ID, "intake_started", "начато заполнение"); err != nil {
		t.Fatalf("ошибка записи журнала: %v", err)
	}
	if err := store.LogEvent(app.ID, "submitted", "передано координатору"); err != nil {
		t.Fatalf("ошибка записи журнала: %v", err)
	}

	events, err := store.Events(app.ID)
	if err != nil {
		t.Fatalf("ошибка выборки журнала: %v", err)
	}
	if len(events) != 2 || events[0].Type != "intake_started" {
		t.Fatalf("журнал должен восстанавливать историю случая: %+v", events)
	}
}

func TestConcurrentPublicIDsAreUnique(t *testing.T) {
	// Файловая база: параллельные транзакции должны сериализоваться счётчиком
	dir := t.TempDir()
	store, err := Open("sqlite://" + dir + "/maxbot.db")
	if err != nil {
		t.Fatalf("не удалось открыть базу: %v", err)
	}
	t.Cleanup(func() {
		if err := store.Close(); err != nil {
			t.Logf("ошибка закрытия базы: %v", err)
		}
	})

	const workers = 100
	day := time.Date(2026, 8, 19, 10, 0, 0, 0, time.UTC)

	var wg sync.WaitGroup
	ids := make([]string, workers)
	errs := make([]error, workers)

	wg.Add(workers)
	for i := 0; i < workers; i++ {
		go func(i int) {
			defer wg.Done()
			ids[i], errs[i] = store.NextPublicID(day)
		}(i)
	}
	wg.Wait()

	seen := make(map[string]bool, workers)
	for i, id := range ids {
		if errs[i] != nil {
			t.Fatalf("выдача номера завершилась ошибкой: %v", errs[i])
		}
		if seen[id] {
			t.Fatalf("номер %s выдан дважды", id)
		}
		seen[id] = true
	}

	if len(seen) != workers {
		t.Fatalf("ожидалось %d уникальных номеров, получено %d", workers, len(seen))
	}
}

func TestMarkProcessedIsIdempotent(t *testing.T) {
	store := newTestStorage(t)

	fresh, err := store.MarkProcessed("cb:42")
	if err != nil || !fresh {
		t.Fatalf("первое событие должно считаться новым: %v %v", fresh, err)
	}

	fresh, err = store.MarkProcessed("cb:42")
	if err != nil {
		t.Fatalf("повторная отметка не должна возвращать ошибку: %v", err)
	}
	if fresh {
		t.Fatal("повторно доставленное событие не должно обрабатываться")
	}

	if err := store.CleanupProcessed(time.Now().Add(time.Hour)); err != nil {
		t.Fatalf("ошибка очистки отметок: %v", err)
	}

	fresh, err = store.MarkProcessed("cb:42")
	if err != nil || !fresh {
		t.Fatalf("после очистки событие снова считается новым: %v %v", fresh, err)
	}
}

func TestActionsLifecycle(t *testing.T) {
	store := newTestStorage(t)

	app := &models.Application{UserID: 5, Status: models.StatusSubmitted, PublicID: "MAX-20260819-0005"}
	if err := store.Create(app); err != nil {
		t.Fatalf("ошибка создания обращения: %v", err)
	}

	now := time.Now()
	due := []time.Time{now.Add(-time.Hour), now.Add(7 * 24 * time.Hour), now.Add(30 * 24 * time.Hour)}
	types := []string{models.ActionInitialContact, models.ActionFollowUp7d, models.ActionFollowUp30d}

	for i, actionType := range types {
		err := store.CreateAction(&models.Action{
			ApplicationID: app.ID, PublicID: app.PublicID, Type: actionType,
			Owner: "координатор", DueAt: due[i], Status: models.ActionStatusOpen,
		})
		if err != nil {
			t.Fatalf("ошибка создания действия: %v", err)
		}
	}

	open, err := store.OpenActions(10)
	if err != nil {
		t.Fatalf("ошибка выборки действий: %v", err)
	}
	if len(open) != 3 {
		t.Fatalf("ожидалось три открытых действия, получено %d", len(open))
	}

	dueNow, err := store.DueActions(now, now, 10)
	if err != nil {
		t.Fatalf("ошибка выборки наступивших действий: %v", err)
	}
	if len(dueNow) != 1 || dueNow[0].Type != models.ActionInitialContact {
		t.Fatalf("наступил срок только одного действия: %+v", dueNow)
	}

	if err := store.MarkReminded(&dueNow[0], now); err != nil {
		t.Fatalf("ошибка отметки напоминания: %v", err)
	}

	repeated, err := store.DueActions(now, now.Add(-time.Hour), 10)
	if err != nil {
		t.Fatalf("ошибка повторной выборки: %v", err)
	}
	if len(repeated) != 0 {
		t.Fatal("о действии уже напомнили, повторное напоминание не отправляется")
	}

	action := &dueNow[0]
	completed := now.Add(time.Minute)
	action.Status = models.ActionStatusDone
	action.CompletedAt = &completed
	action.Result = "связались, помощь начата"
	if err := store.SaveAction(action); err != nil {
		t.Fatalf("ошибка закрытия действия: %v", err)
	}

	open, err = store.OpenActions(10)
	if err != nil {
		t.Fatalf("ошибка выборки действий: %v", err)
	}
	if len(open) != 2 {
		t.Fatalf("после закрытия должно остаться два действия, получено %d", len(open))
	}
}

func TestDatabaseFilePermissions(t *testing.T) {
	dir := t.TempDir()
	path := dir + "/pii/maxbot.db"

	store, err := Open("sqlite://" + path)
	if err != nil {
		t.Fatalf("не удалось открыть базу: %v", err)
	}
	t.Cleanup(func() {
		if err := store.Close(); err != nil {
			t.Logf("ошибка закрытия базы: %v", err)
		}
	})

	dirInfo, err := os.Stat(dir + "/pii")
	if err != nil {
		t.Fatalf("ошибка проверки каталога: %v", err)
	}
	if perm := dirInfo.Mode().Perm(); perm&0o077 != 0 {
		t.Fatalf("каталог с персональными данными доступен посторонним: %o", perm)
	}

	fileInfo, err := os.Stat(path)
	if err != nil {
		t.Fatalf("ошибка проверки файла базы: %v", err)
	}
	if perm := fileInfo.Mode().Perm(); perm&0o077 != 0 {
		t.Fatalf("файл базы доступен посторонним: %o", perm)
	}
}
