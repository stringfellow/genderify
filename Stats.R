library(DBI)
library(magrittr)
con <-dbConnect(RSQLite::SQLite(), "genderify.db")

dbListTables(con)

dbReadTable(con, "artists") %>% View

dbReadTable(con, "artists")$gender %>% table %>% plot


# Each individual member of a group is also in the database
# set is_group == 0 for example

# Ignore lead gender

